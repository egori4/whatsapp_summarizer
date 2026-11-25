"""Offline, deterministic weekly roll-up acceptance tests. No real emails or model calls."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from whatsapp_digest.config import ConfigError, load_config
from whatsapp_digest.database import DigestDatabase
from whatsapp_digest.delivery.base import DeliveryError
from whatsapp_digest.model.hermes import HermesGatewayError, ModelInvocationResult
from whatsapp_digest.runner import RunnerError
from whatsapp_digest.validation import DigestValidationError, validate_weekly_digest
from whatsapp_digest.weekly import (
    SECTION_TITLES, WEEKLY_SYSTEM_PROMPT, _select_covered, build_evidence_cards,
    resolve_period, run_weekly,
)


NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
ZONE = ZoneInfo("America/Toronto")
CONFIG = """
paths:
  database: ./data/test.db
  log: ./data/test.log
retention:
  processed_raw_days: 7
  log_days: 30
  digest_days: 30
hermes:
  timeout_seconds: 5
whatsapp:
  bridge_url: http://127.0.0.1:3001
email:
  host: localhost
  sender: test@example.com
  password_env: TEST_SMTP_PASSWORD
defaults:
  timezone: America/Toronto
workflows:
  - id: global-ps
    name: Global PS
    group_jid: "120363429012626317@g.us"
    schedule:
      type: daily
      at: "07:30"
    model:
      provider: inherit
      name: inherit
      reasoning: high
    summarization:
      sections:
        - id: technical_updates
          title: Technical Updates
    weekly_summary:
      enabled: true
      schedule:
        type: weekly
        days: [mon]
        at: "08:30"
    delivery:
      email:
        to: [test-recipient@example.com]
        cc: []
        bcc: [test-blind@example.com]
        attach_raw_messages: true
"""


@pytest.fixture()
def cfg(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return load_config(path)


def make_card(
    title="Wildcard host limitation",
    summary="AppWall wildcard does not cover apex domain.",
    names=("Artur",),
):
    return {
        "title": title, "summary": summary, "details": ["Lab tested, not production resolved"],
        "actions": [], "questions": [],
        "reported_by": list(names), "contributors": [],
        "references": ["https://example.org/knowledge"], "source_ids": ["wa-message-id"],
    }


def digest(*items):
    return {
        "sections": [{
            "section_id": "technical_updates", "title": "Technical Updates",
            "items": list(items),
        }] if items else [],
    }


def ts(day: date, hour=7, minute=31):
    return datetime.combine(day, time(hour, minute), ZONE).astimezone(timezone.utc).isoformat()


def seed_artifact(db, run_id, day, before, after, *, output=None, hour=7, minute=31,
                  workflow="global-ps"):
    stamp = ts(day, hour, minute)
    db.create_run(run_id=run_id, workflow_id=workflow, mode="scheduled",
                  checkpoint_before=before, cutoff_seq=after, message_count=1)
    with db._tx() as conn:
        conn.execute(
            "UPDATE runs SET status='delivered', completed_at=? WHERE run_id=?",
            (stamp, run_id),
        )
        conn.execute(
            """INSERT INTO daily_digest_artifacts
               (run_id, workflow_id, completed_at, checkpoint_before, checkpoint_after,
                outcome, window_start, window_end, digest_json)
               VALUES (?, ?, ?, ?, ?, 'delivered', ?, ?, ?)""",
            (run_id, workflow, stamp, before, after, stamp, stamp,
             json.dumps(output if output is not None else digest(make_card()))),
        )


def seed_week(cfg, *, only_days=None, include_anchor=True, content=True):
    monday = date(2026, 11, 2)
    with DigestDatabase(cfg.database_path) as db:
        if include_anchor:
            seed_artifact(db, "anchor", monday, 0, 1, output=digest())
        selected = only_days if only_days is not None else [1, 2, 3, 4, 5, 6, 7]
        checkpoint = 1
        for offset in selected:
            if offset == 7:
                day = date(2026, 11, 9)
            else:
                day = monday + timedelta(days=offset)
            nxt = checkpoint + 1
            seed_artifact(
                db, f"day{offset}", day, checkpoint, nxt,
                output=digest(make_card()) if content and offset != 2 else digest(),
            )
            checkpoint = nxt


def weekly_item(ids, **overrides):
    item = {
        "title": "AppWall wildcard behavior",
        "summary": "Wildcard matches subdomains but not apex; lab-tested only.",
        "details": [], "takeaway": "Check the host pattern before rollout.",
        "actions": [], "questions": [],
        "reported_by": ["Artur"], "contributors": [],
        "references": ["https://example.org/knowledge"], "source_ids": ids,
    }
    item.update(overrides)
    return item


def weekly_output(ids, *, section="alteon_appwall", **changes):
    return {"sections": [{
        "section_id": section, "title": "generated title",
        "items": [weekly_item(ids, **changes)],
    }]}


class FakeGateway:
    def __init__(self, payload=None, fail=None):
        self.payload = payload
        self.fail = fail
        self.called = 0
        self.user_prompt = None
        self.system_prompt = None
        self.schema = None
        self.workflow = None

    def generate_prompt(self, workflow, *, system_prompt, user_prompt, schema):
        self.called += 1
        self.workflow = workflow
        self.user_prompt = user_prompt
        self.system_prompt = system_prompt
        self.schema = schema
        if self.fail is not None:
            raise self.fail
        if self.payload is None:
            cards = json.loads(user_prompt.split("DAILY EVIDENCE CARDS:\n", 1)[1])
            result = weekly_output([cards[0]["source_id"]])
        else:
            result = self.payload
        return ModelInvocationResult(
            raw_output=json.dumps(result), provider="fake", model="fixture",
            reasoning="none",
        )


class FakeDelivery:
    def __init__(self, fail=None):
        self.sent = []
        self.failures = []
        self.fail = fail

    def send_digest(self, workflow, **kwargs):
        self.sent.append((workflow, kwargs))
        if self.fail:
            raise self.fail

    def send_failure(self, workflow, **kwargs):
        self.failures.append((workflow, kwargs))


def test_week_dates_and_dst(cfg):
    w = cfg.workflow("global-ps")
    p = resolve_period(w, now=NOW, period="2026-11-02")
    assert p.week_start.isoformat() == "2026-11-02"
    assert p.week_end.isoformat() == "2026-11-09"
    assert (p.end_utc - p.start_utc) == timedelta(hours=168)
    spring = resolve_period(w, now=datetime(2026, 3, 11, tzinfo=timezone.utc),
                            period="2026-03-02")
    fall = resolve_period(w, now=NOW, period="2026-10-26")
    assert (spring.end_utc - spring.start_utc) == timedelta(hours=167)
    assert (fall.end_utc - fall.start_utc) == timedelta(hours=169)


@pytest.mark.parametrize("period", ["2026-11-03", "2026-11-16", "tomorrow", "2026/11/02"])
def test_week_rejects_bad_or_future_period(cfg, period):
    with pytest.raises(RunnerError):
        resolve_period(cfg.workflow("global-ps"), now=NOW, period=period)


def test_default_week_monday_cutoff(cfg):
    w = cfg.workflow("global-ps")
    assert resolve_period(w, now=NOW).week_start.isoformat() == "2026-11-02"
    before_cutoff = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
    assert resolve_period(w, now=before_cutoff).week_start.isoformat() == "2026-10-26"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.replace("days: [mon]", "days: [fri]"),
        lambda s: s.replace("days: [mon]", "days: [mon, wed]"),
        lambda s: s.replace('at: "08:30"', 'at: "07:00"'),
        lambda s: s.replace("digest_days: 30", "digest_days: 7"),
        lambda s: s.replace("enabled: true", "enabled: invalid"),
        lambda s: s.replace("type: weekly", "type: monthly"),
    ],
)
def test_invalid_weekly_config_rejected(tmp_path, mutation):
    path = tmp_path / "config.yaml"
    path.write_text(mutation(CONFIG))
    with pytest.raises(ConfigError):
        load_config(path)


def test_weekly_disabled_by_default_old_configuration(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.split("    weekly_summary:")[0] +
                    "    delivery:\n      email:\n        to: [test@example.com]\n")
    config = load_config(path)
    assert config.workflow("global-ps").weekly_summary["enabled"] is False


def test_coverage_and_successful_weekly_delivery(cfg):
    seed_week(cfg)
    gateway, email = FakeGateway(), FakeDelivery()
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert result.status == "weekly-delivered"
    assert result.message_count == 6
    assert gateway.called == 1
    assert gateway.workflow.sections[0].id == "cyber_controller"
    assert len(email.sent) == 1
    assert email.sent[0][1]["raw_text"] is None
    assert "Weekly" in email.sent[0][1]["subject"]
    assert "2026-11-09" in email.sent[0][1]["subject"]
    assert "Daily digest dates" not in email.sent[0][1]["text"]
    assert "Alteon / AppWall" in email.sent[0][1]["text"]
    assert "Check the host pattern" in email.sent[0][1]["text"]
    assert email.sent[0][0].email["bcc"] == ["test-blind@example.com"]
    with DigestDatabase(cfg.database_path) as db:
        assert db.weekly_report("global-ps", "2026-11-02") is not None
        assert db.weekly_report("global-ps", "2026-11-02")["input_run_ids_json"].count("day") == 7
        assert db.workflow_state("global-ps")["initialized"] == 0
        assert db._conn.execute(
            "SELECT status FROM runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0] == "weekly-delivered"


def test_weekly_second_run_is_idempotent(cfg):
    seed_week(cfg)
    first_email = FakeDelivery()
    run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=first_email)
    gateway, email = FakeGateway(), FakeDelivery()
    second = run_weekly(cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert second.status == "already-delivered"
    assert gateway.called == 0 and not email.sent
    assert len(first_email.sent) == 1


def test_weekly_delivered_period_can_be_reformatted_only_as_dry_run(cfg):
    seed_week(cfg)
    first = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    email = FakeDelivery()
    result = run_weekly(cfg, "global-ps", now=NOW, dry_run=True,
                        gateway=FakeGateway(), delivery=email)
    assert result.status == "weekly-dry-run-success"
    assert result.rendered_text
    assert not email.sent
    with DigestDatabase(cfg.database_path) as db:
        assert db.weekly_report("global-ps", "2026-11-02")["run_id"] == first.run_id


def test_weekly_dry_run_no_email_no_ledger(cfg):
    seed_week(cfg)
    email = FakeDelivery()
    result = run_weekly(cfg, "global-ps", now=NOW, dry_run=True,
                        gateway=FakeGateway(), delivery=email)
    assert result.status == "weekly-dry-run-success"
    assert result.rendered_html and "Global PS:" in result.rendered_html
    assert result.raw_text is None
    assert not email.sent
    with DigestDatabase(cfg.database_path) as db:
        assert db.weekly_report("global-ps", "2026-11-02") is None


def test_weekly_first_week_no_anchor_fails_closed(cfg):
    seed_week(cfg, include_anchor=False)
    gateway, email = FakeGateway(), FakeDelivery()
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert result.status == "insufficient-history"
    assert gateway.called == 0 and not email.sent


def test_missing_current_monday_fails_closed(cfg):
    seed_week(cfg, only_days=[1, 2, 3, 4, 5, 6])
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    assert result.status == "daily-not-complete"


def test_missing_intermediate_day_with_catchup_contiguous_passes(cfg):
    seed_week(cfg, only_days=[1, 7])
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    assert result.status == "weekly-delivered"


def test_broken_chain_fails_closed(cfg):
    seed_week(cfg)
    with DigestDatabase(cfg.database_path) as db:
        db._conn.execute("UPDATE daily_digest_artifacts SET checkpoint_before=999 WHERE run_id='day4'")
        db._conn.commit()
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    assert result.status == "broken-checkpoint-chain"


def test_other_workflow_artifact_not_selected(cfg):
    seed_week(cfg)
    with DigestDatabase(cfg.database_path) as db:
        seed_artifact(db, "foreign", date(2026, 11, 5), 0, 99, workflow="foreign")
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    assert result.status == "weekly-delivered" and result.message_count == 6


def test_monday_after_cutoff_not_included(cfg):
    seed_week(cfg, only_days=[1, 2, 3, 4, 5, 6])
    with DigestDatabase(cfg.database_path) as db:
        seed_artifact(db, "late", date(2026, 11, 9), 7, 8, hour=9, minute=5)
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    assert result.status == "daily-not-complete"


def test_successful_empty_week_is_delivered_without_model(cfg):
    seed_week(cfg, content=False)
    gateway, email = FakeGateway(), FakeDelivery()
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert result.status == "weekly-delivered" and result.message_count == 0
    assert gateway.called == 0
    assert "No material updates" in email.sent[0][1]["text"]


def test_card_id_deterministic_and_provenance(cfg):
    seed_week(cfg)
    with DigestDatabase(cfg.database_path) as db:
        p = resolve_period(cfg.workflow("global-ps"), now=NOW)
        selected, err = _select_covered(db, cfg.workflow("global-ps"), p)
        assert err is None
        cards = build_evidence_cards(selected, cfg.workflow("global-ps"))
        assert cards == build_evidence_cards(selected, cfg.workflow("global-ps"))
        assert cards[0]["source_id"] == "daily:day1:technical_updates:0"
        assert cards[0]["original_message_ids"] == ["wa-message-id"]


@pytest.mark.parametrize(
    "change",
    [
        {"source_ids": ["daily:not-available:technical_updates:0"]},
        {"reported_by": ["Invented Engineer"]},
        {"references": ["https://malicious.example/fake"]},
        {"summary": "Read https://invented.example/this next."},
    ],
)
def test_weekly_validator_rejects_ungrounded_output(cfg, change):
    seed_week(cfg)
    with DigestDatabase(cfg.database_path) as db:
        p = resolve_period(cfg.workflow("global-ps"), now=NOW)
        cards = build_evidence_cards(_select_covered(db, cfg.workflow("global-ps"), p)[0],
                                     cfg.workflow("global-ps"))
    fake = weekly_output([cards[0]["source_id"]], **change)
    with pytest.raises(DigestValidationError):
        validate_weekly_digest(json.dumps(fake), cards, SECTION_TITLES)


def test_weekly_validator_rejects_wrong_section(cfg):
    seed_week(cfg)
    gateway, email = FakeGateway(weekly_output(["daily:day1:technical_updates:0"], section="unknown")), FakeDelivery()
    with pytest.raises(RunnerError, match="weekly validation failed"):
        run_weekly(cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert not email.sent


def test_weekly_merges_multiple_cited_days(cfg):
    seed_week(cfg)
    ids = ["daily:day1:technical_updates:0", "daily:day5:technical_updates:0"]
    fake = FakeGateway(weekly_output(ids))
    result = run_weekly(cfg, "global-ps", now=NOW, gateway=fake, delivery=FakeDelivery())
    assert result.status == "weekly-delivered"
    assert len(result.digest["sections"][0]["items"]) == 1
    assert result.digest["sections"][0]["items"][0]["evidence_dates"] == ["2026-11-03", "2026-11-07"]


def test_weekly_fake_prompt_security_and_provider_schema(cfg):
    seed_week(cfg)
    fake = FakeGateway()
    run_weekly(cfg, "global-ps", now=NOW, dry_run=True, gateway=fake, delivery=FakeDelivery())
    assert "ignore" in fake.system_prompt.lower()
    assert "lab success is not production" in fake.system_prompt.lower()
    assert "seven configured" in fake.system_prompt.lower()
    assert "sections" in fake.schema["properties"]


def test_weekly_product_family_categories_and_alias_prompt():
    from whatsapp_digest.weekly import WEEKLY_SECTIONS
    from whatsapp_digest.model.hermes import BASE_SYSTEM_PROMPT
    ids = [section.id for section in WEEKLY_SECTIONS]
    assert ids == [
        "cyber_controller", "defensepro_dpx", "alteon_appwall",
        "cloud_security", "other_products", "cross_product", "team_notes",
    ]
    assert SECTION_TITLES["cyber_controller"] == "Cyber Controller / Management"
    assert SECTION_TITLES["defensepro_dpx"] == "DefensePro / DefensePro X"
    assert SECTION_TITLES["cloud_security"] == "Cloud Security"
    for alias in (
        "DP = DefensePro", "DPX = DefensePro X",
        "CC = Cyber Controller", "CC+ = Cyber Controller Plus",
        "CWAAP refers to Cloud Application Protection Service",
    ):
        assert alias in BASE_SYSTEM_PROMPT
        assert alias in WEEKLY_SYSTEM_PROMPT
    assert "PRIMARY technical subject" in WEEKLY_SYSTEM_PROMPT
    assert "Grouping does NOT mean product variants have identical capabilities" in WEEKLY_SYSTEM_PROMPT
    assert "A CC+ requirement compared with DefensePro capacity is normally CC/Management" in WEEKLY_SYSTEM_PROMPT
    assert "Cloud Firewall-as-a-Service" in WEEKLY_SYSTEM_PROMPT
    assert "APSolute Vision" in WEEKLY_SYSTEM_PROMPT


def test_weekly_product_format_single_family_and_distinct_limits():
    from whatsapp_digest.renderer import RenderContext, render_digest
    from dataclasses import replace
    from datetime import datetime
    ctx = RenderContext(
        workflow_name="Global PS Technical Digest", workflow_id="global-ps",
        run_id="test", generated_at=datetime(2026, 11, 9, 9, 0),
        timezone="America/Toronto", provider="fixture", model="test",
        reasoning="none", message_count=2, window_start=None, window_end=None,
        weekly_period="PARTIAL Nov 5-9", weekly_label="Nov 5–9, 2026 (PARTIAL)",
    )
    one = weekly_item(["daily:one:technical_updates:0"],
                      title="CC+ DNS allowlist", summary="CC+ limit: 100,000.",
                      details=["This is a CC+ protection limit."],
                      takeaway="Validate the required record count.",
                      questions=["Is a higher CC+ limit planned?"],
                      evidence_dates=["2026-11-06"])
    two = weekly_item(["daily:two:technical_updates:0"],
                      title="Cyber Controller X", summary="Distinct CC X behavior.",
                      details=[], takeaway="", questions=[],
                      evidence_dates=["2026-11-07"])
    result = render_digest({"sections": [
        {"section_id": "cyber_controller",
         "title": "Cyber Controller / Management", "items": [one, two]},
        {"section_id": "cloud_security",
         "title": "Cloud Security", "items": []},
    ]}, ctx)
    assert result.text.count("Cyber Controller / Management") == 1
    assert "CC+ limit: 100,000." in result.text
    assert "Distinct CC X behavior." in result.text
    assert "  → Validate the required record count." in result.text
    assert "  Open: Is a higher CC+ limit planned?" in result.text
    assert "Daily digest dates:" not in result.text
    assert "Cloud Security" not in result.text
    assert "2026-11-06" not in result.text
    assert result.html.count("<h2>Cyber Controller / Management</h2>") == 1
    assert '<ul class="weekly-topics">' in result.html
    assert "Cloud Security" not in result.html


def test_weekly_model_failure_does_not_commit(cfg):
    seed_week(cfg)
    email = FakeDelivery()
    with pytest.raises(RunnerError, match="weekly model failed"):
        run_weekly(cfg, "global-ps", now=NOW,
                   gateway=FakeGateway(fail=HermesGatewayError("failed")),
                   delivery=email)
    assert not email.sent
    with DigestDatabase(cfg.database_path) as db:
        assert db.weekly_report("global-ps", "2026-11-02") is None
        assert db.workflow_state("global-ps")["initialized"] == 0


def test_weekly_smtp_failure_retry(cfg):
    seed_week(cfg)
    with pytest.raises(RunnerError, match="delivery failed"):
        run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(),
                   delivery=FakeDelivery(DeliveryError("refused")))
    with DigestDatabase(cfg.database_path) as db:
        assert db.weekly_report("global-ps", "2026-11-02") is None
    retry = run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    assert retry.status == "weekly-delivered"


def test_oversized_weekly_input_fails_without_truncation(cfg):
    seed_week(cfg)
    with DigestDatabase(cfg.database_path) as db:
        big = digest(make_card(summary="x" * 175000))
        db._conn.execute("UPDATE daily_digest_artifacts SET digest_json=? WHERE run_id='day1'", (json.dumps(big),))
        db._conn.commit()
    gateway, email = FakeGateway(), FakeDelivery()
    with pytest.raises(RunnerError, match="safe input limit"):
        run_weekly(cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert gateway.called == 0 and not email.sent


def test_digest_retention_keeps_tiny_delivery_ledger(cfg):
    seed_week(cfg)
    run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with DigestDatabase(cfg.database_path) as db:
        assert db.purge_digest_artifacts(30, now=later) >= 7
        ledger = db.weekly_report("global-ps", "2026-11-02")
        assert ledger is not None and ledger["digest_json"] is None
        assert ledger["input_run_ids_json"] == "[]"


def test_weekly_rejects_disabled_workflow(tmp_path):
    path=tmp_path/"cfg.yaml"
    path.write_text(CONFIG.replace("enabled: true", "enabled: false").replace(
        "    weekly_summary:\n      enabled: false\n      schedule:\n        type: weekly\n        days: [mon]\n        at: \"08:30\"\n", ""
    ))
    cfg=load_config(path)
    with pytest.raises(RunnerError, match="disabled"):
        run_weekly(cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=FakeDelivery())


def test_no_david_email_in_fixture(cfg):
    email = cfg.workflow("global-ps").email
    addresses = email["to"] + email["cc"] + email["bcc"]
    assert all("david.medina" not in address.lower() for address in addresses)


# One-time November 9 bootstrap: a named, explicitly incomplete reporting period.
# Production may start saving canonical daily artifacts on November 5, so an
# earlier-Monday anchor does not exist and must never be fabricated.
PARTIAL_CONFIG = CONFIG.replace(
    "    weekly_summary:\n      enabled: true\n",
    "    weekly_summary:\n      enabled: true\n"
    '      first_partial_week: "2026-11-02"\n'
    "      first_partial_week_to_only: true\n",
)


@pytest.fixture()
def partial_cfg(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(PARTIAL_CONFIG)
    return load_config(path)


def test_partial_first_week_no_anchor_is_delivered_to_primary_only(partial_cfg):
    seed_week(partial_cfg, include_anchor=False, only_days=[3, 4, 5, 6, 7])
    gateway, email = FakeGateway(), FakeDelivery()
    result = run_weekly(partial_cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert result.status == "weekly-delivered"
    assert result.message_count == 5
    assert len(email.sent) == 1
    target, kwargs = email.sent[0]
    assert target.email["to"] == ["test-recipient@example.com"]
    assert target.email["cc"] == []
    assert target.email["bcc"] == []
    # The real daily parent workflow and its BCC remain completely untouched.
    assert partial_cfg.workflow("global-ps").email["bcc"] == ["test-blind@example.com"]
    assert "PARTIAL" in kwargs["subject"]
    assert "2026-11-09" in kwargs["subject"]
    assert "PARTIAL" in kwargs["text"]
    assert "2026-11-05" in kwargs["text"]
    assert "earlier days unavailable" in kwargs["text"]
    assert "PARTIAL" in kwargs["html"]
    assert "PARTIAL COVERAGE" in gateway.user_prompt
    assert kwargs["raw_text"] is None
    with DigestDatabase(partial_cfg.database_path) as db:
        assert db.weekly_report("global-ps", "2026-11-02") is not None


def test_partial_first_week_idempotent(partial_cfg):
    seed_week(partial_cfg, include_anchor=False, only_days=[3, 4, 5, 6, 7])
    email = FakeDelivery()
    assert run_weekly(partial_cfg, "global-ps", now=NOW, gateway=FakeGateway(),
                      delivery=email).status == "weekly-delivered"
    second, second_email = FakeGateway(), FakeDelivery()
    assert run_weekly(partial_cfg, "global-ps", now=NOW, gateway=second,
                      delivery=second_email).status == "already-delivered"
    assert second.called == 0
    assert not second_email.sent
    assert len(email.sent) == 1


def test_partial_first_week_requires_monday_daily_completion(partial_cfg):
    seed_week(partial_cfg, include_anchor=False, only_days=[3, 4, 5, 6])
    email = FakeDelivery()
    r = run_weekly(partial_cfg, "global-ps", now=NOW, gateway=FakeGateway(), delivery=email)
    assert r.status == "daily-not-complete"
    assert not email.sent


def test_partial_first_week_rejects_missing_artifacts(partial_cfg):
    with DigestDatabase(partial_cfg.database_path):
        pass
    result = run_weekly(partial_cfg, "global-ps", now=NOW,
                        gateway=FakeGateway(), delivery=FakeDelivery())
    assert result.status == "daily-not-complete"


def test_partial_first_week_still_rejects_broken_chain(partial_cfg):
    seed_week(partial_cfg, include_anchor=False, only_days=[3, 4, 5, 6, 7])
    with DigestDatabase(partial_cfg.database_path) as db:
        db._conn.execute("UPDATE daily_digest_artifacts SET checkpoint_before=999 WHERE run_id='day6'")
        db._conn.commit()
    gateway, email = FakeGateway(), FakeDelivery()
    result = run_weekly(partial_cfg, "global-ps", now=NOW,
                        gateway=gateway, delivery=email)
    assert result.status == "broken-checkpoint-chain"
    assert gateway.called == 0 and not email.sent


def test_partial_first_week_no_model_failure_notification_to_bcc(partial_cfg):
    seed_week(partial_cfg, include_anchor=False, only_days=[3, 4, 5, 6, 7])
    gateway = FakeGateway(fail=HermesGatewayError("test"))
    email = FakeDelivery()
    with pytest.raises(RunnerError, match="weekly model failed"):
        run_weekly(partial_cfg, "global-ps", now=NOW, gateway=gateway, delivery=email)
    assert not email.sent
    assert len(email.failures) == 1
    assert email.failures[0][0].email["bcc"] == []
    assert email.failures[0][0].email["cc"] == []


def test_next_week_requires_anchor_and_uses_normal_bcc(partial_cfg):
    seed_week(partial_cfg, include_anchor=False, only_days=[3, 4, 5, 6, 7])
    with DigestDatabase(partial_cfg.database_path) as db:
        before = 6
        for n in range(7):
            day = date(2026, 11, 10) + timedelta(days=n)
            seed_artifact(db, f"next-{n}", day, before, before + 1)
            before += 1
    now = datetime(2026, 11, 16, 15, tzinfo=timezone.utc)
    gateway, email = FakeGateway(), FakeDelivery()
    result = run_weekly(partial_cfg, "global-ps", period="2026-11-09", now=now,
                        gateway=gateway, delivery=email)
    assert result.status == "weekly-delivered"
    assert "PARTIAL" not in email.sent[0][1]["subject"]
    assert "PARTIAL" not in email.sent[0][1]["text"]
    assert email.sent[0][0].email["bcc"] == ["test-blind@example.com"]
    assert "PARTIAL COVERAGE" not in gateway.user_prompt


def test_other_unanchored_week_cannot_use_partial_exception(partial_cfg):
    with DigestDatabase(partial_cfg.database_path) as db:
        for n in range(7):
            day = date(2026, 11, 10) + timedelta(days=n)
            seed_artifact(db, f"next-{n}", day, n, n + 1)
    result = run_weekly(
        partial_cfg, "global-ps", period="2026-11-09",
        now=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
        gateway=FakeGateway(), delivery=FakeDelivery(),
    )
    assert result.status == "insufficient-history"


def test_config_first_partial_week_strict_validation(tmp_path):
    replacements = [
        '      first_partial_week: "2026-11-03"\n',  # Tuesday
        '      first_partial_week: "November 2"\n',
        '      first_partial_week: 2026-11-02\n',  # YAML date object, not string
        '      first_partial_week_to_only: "yes"\n',
    ]
    for replacement in replacements:
        edited = PARTIAL_CONFIG
        if "first_partial_week_to_only:" in replacement:
            edited = edited.replace("      first_partial_week_to_only: true\n", replacement)
        else:
            edited = edited.replace('      first_partial_week: "2026-11-02"\n', replacement)
        path = tmp_path / "config.yaml"
        path.write_text(edited)
        with pytest.raises(ConfigError):
            load_config(path)


def test_config_to_only_flag_requires_named_partial_week(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.replace(
        "    weekly_summary:\n      enabled: true\n",
        "    weekly_summary:\n      enabled: true\n      first_partial_week_to_only: true\n",
    ))
    with pytest.raises(ConfigError, match="requires first_partial_week"):
        load_config(path)
