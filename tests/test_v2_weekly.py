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
        "details": [], "actions": [], "questions": [],
        "reported_by": ["Artur"], "contributors": [],
        "references": ["https://example.org/knowledge"], "source_ids": ids,
    }
    item.update(overrides)
    return item


def weekly_output(ids, *, section="key_learnings", **changes):
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
    assert gateway.workflow.sections[0].id == "key_learnings"
    assert len(email.sent) == 1
    assert email.sent[0][1]["raw_text"] is None
    assert "Weekly" in email.sent[0][1]["subject"]
    assert "2026-11-09" in email.sent[0][1]["subject"]
    assert "Daily digest dates" in email.sent[0][1]["text"]
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


def test_weekly_dry_run_no_email_no_ledger(cfg):
    seed_week(cfg)
    email = FakeDelivery()
    result = run_weekly(cfg, "global-ps", now=NOW, dry_run=True,
                        gateway=FakeGateway(), delivery=email)
    assert result.status == "weekly-dry-run-success"
    assert result.rendered_html and "Weekly Review" in result.rendered_html
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
    assert "only the three" in fake.system_prompt.lower()
    assert "sections" in fake.schema["properties"]


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
