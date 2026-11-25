"""v2.6 raw-first weekly resolution tests; no external model or email."""
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from whatsapp_digest.config import ConfigError, load_config
from whatsapp_digest.database import DigestDatabase
from whatsapp_digest.runner import RunnerError
from whatsapp_digest.validation import DigestValidationError
from whatsapp_digest.weekly import run_weekly, resolve_period, _select_covered
from whatsapp_digest.weekly_resolution import raw_evidence, validate_resolution
from test_v2_weekly import CONFIG, NOW, FakeGateway, FakeDelivery, seed_artifact, ts, digest, make_card


@pytest.fixture
def hybrid(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG.replace("processed_raw_days: 7", "processed_raw_days: 14")
                 .replace("weekly_summary:\n      enabled: true",
                          "weekly_summary:\n      enabled: true\n      evidence_mode: hybrid"))
    return load_config(p)


def seed(hybrid):
    base = date(2026, 11, 2)
    messages = [
        ("historical", "Artur", "Earlier context for the issue."),
        ("ask", "Artur", "Does AppWall wildcard cover the apex domain?"),
        ("proposal", "Nicola", "Try a separate exact host entry."),
        ("confirmed", "Artur", "Verified in lab: wildcard covers subdomains only; exact host matches apex."),
        ("open", "Juan", "What is the CC Azure OS/data VHD procedure?"),
        ("partial", "Juan", "Can I deploy without a separate data VHD?"),
        ("reply", "Nicola", "The OS disk deploys successfully; data disk still untested."),
        ("new", "Artur", "No further technical updates."),
    ]
    with DigestDatabase(hybrid.database_path) as db:
        for i, (mid, author, text) in enumerate(messages):
            day = base + timedelta(days=i)
            db.upsert_message(message_id=mid, group_jid=hybrid.workflow("global-ps").group_jid,
                              sender_id=None, display_name=author, occurred_at=ts(day, 7, 0), text=text)
            seed_artifact(db, "anchor" if i == 0 else f"day{i}", day, i, i+1,
                          output=digest(make_card()) if i == 1 else digest())
        db.set_checkpoint("global-ps", 8)
    return messages


def item(**kwargs):
    result = dict(title="Wildcard host matching", summary="Does wildcard cover the apex?",
                  product="alteon_appwall", status="confirmed",
                  resolution="Lab verified wildcard covers subdomains only; use exact host for apex.",
                  resolution_source_ids=["confirmed"], takeaway="Configure apex separately.",
                  details=[], questions=[], actions=[], reported_by=["Artur"],
                  contributors=["Nicola"], references=[],
                  source_ids=["ask", "proposal", "confirmed"])
    result.update(kwargs)
    return result


def unresolved(**kwargs):
    result = item(title="Azure VHD deployment", summary="How should CC be deployed?",
                  product="cyber_controller", status="unanswered", resolution="",
                  resolution_source_ids=[], takeaway="", questions=["What is the correct OS/data VHD deployment procedure?"],
                  reported_by=["Juan"], contributors=[], source_ids=["open"])
    result.update(kwargs)
    return result


def output(resolved=None, opened=None):
    return {"sections": [
        *([{"section_id": "resolved", "title": "ignored", "items": resolved}] if resolved is not None else []),
        *([{"section_id": "unresolved", "title": "ignored", "items": opened}] if opened is not None else []),
    ]}


def sources(hybrid):
    w = hybrid.workflow("global-ps")
    with DigestDatabase(hybrid.database_path) as db:
        period = resolve_period(w, now=NOW, period="2026-11-02")
        artifacts, err = _select_covered(db, w, period)
        assert err is None
        return raw_evidence(db, w, artifacts, period)


def test_raw_first_includes_messages_omitted_by_daily_ai_and_two_sections(hybrid):
    seed(hybrid)
    model = FakeGateway(output([item()], [unresolved()]))
    email = FakeDelivery()
    result = run_weekly(hybrid, "global-ps", period="2026-11-02", now=NOW,
                        dry_run=True, gateway=model, delivery=email)
    assert result.status == "weekly-dry-run-success"
    assert model.called == 1 and result.message_count == 7
    assert "CURRENT RAW MESSAGES" in model.user_prompt
    assert "Verified in lab" in model.user_prompt
    assert "What is the CC Azure" in model.user_prompt
    assert "Earlier context" in model.user_prompt
    assert "hints only; not citable" in model.user_prompt
    assert [s["section_id"] for s in result.digest["sections"]] == ["resolved", "unresolved"]
    assert result.digest["sections"][0]["items"][0]["resolution_source_ids"] == ["confirmed"]
    assert "Resolved Issues" in result.rendered_text and "Unresolved Issues" in result.rendered_text
    assert "Resolved issues: 1 | Unresolved issues: 1" in result.rendered_text
    assert "<strong>Resolved issues: 1</strong>" in result.rendered_html
    assert "<strong>Unresolved issues: 1</strong>" in result.rendered_html
    assert "Resolution:" in result.rendered_text and "Status: Unanswered" in result.rendered_text
    assert "Raw messages analyzed: 7" in result.rendered_text
    assert "Daily digest hints: 1" in result.rendered_text
    assert "<h3>Alteon / AppWall</h3>" in result.rendered_html
    assert not email.sent and not email.failures
    with DigestDatabase(hybrid.database_path) as db:
        assert db.workflow_state("global-ps")["checkpoint_seq"] == 8
        assert db.weekly_report("global-ps", "2026-11-02") is None


def test_hybrid_empty_daily_cards_still_analyzes_raw(hybrid):
    seed(hybrid)
    with DigestDatabase(hybrid.database_path) as db:
        db._conn.execute("UPDATE daily_digest_artifacts SET digest_json=?", ('{"sections":[]}',))
        db._conn.commit()
    model = FakeGateway(output(opened=[unresolved()]))
    result = run_weekly(hybrid, "global-ps", now=NOW, dry_run=True, gateway=model)
    assert model.called == 1 and result.message_count == 7
    assert "Resolved Issues" in result.rendered_text
    assert "None identified in the available evidence." in result.rendered_text


def test_hybrid_missing_raw_fails_before_model(hybrid):
    seed(hybrid)
    with DigestDatabase(hybrid.database_path) as db:
        db._conn.execute("DELETE FROM messages WHERE message_id='confirmed'")
        db._conn.commit()
    model = FakeGateway(output([item()]))
    email = FakeDelivery()
    with pytest.raises(RunnerError, match="raw coverage incomplete"):
        run_weekly(hybrid, "global-ps", now=NOW, dry_run=True, gateway=model, delivery=email)
    assert not model.called and not email.sent and not email.failures


def test_hybrid_edited_after_cutoff_fails_closed(hybrid):
    seed(hybrid)
    with DigestDatabase(hybrid.database_path) as db:
        db.upsert_message(message_id="confirmed", group_jid=hybrid.workflow("global-ps").group_jid,
                          sender_id=None, display_name="Artur", occurred_at=ts(date(2026, 11, 5),7,0),
                          text="Updated after cutoff")
    with pytest.raises(RunnerError, match="raw coverage incomplete"):
        run_weekly(hybrid, "global-ps", now=NOW, dry_run=True, gateway=FakeGateway(output([item()])))


@pytest.mark.parametrize("bad", [
    item(resolution=""),
    item(resolution_source_ids=[]),
    item(resolution_source_ids=["open"]),
    item(resolution_source_ids=["historical"], source_ids=["ask", "historical"], contributors=[]),
    item(status="proposed"),
    item(questions=["Is this actually fixed?"]),
    item(product="DPX"),
    item(reported_by=["Unknown person"]),
    item(source_ids=["daily:day1:technical_updates:0"]),
    item(source_ids=["historical"], resolution_source_ids=["historical"], contributors=[]),
    item(resolution="See https://example.invalid/fix"),
])
def test_invalid_resolved_rejected(hybrid, bad):
    seed(hybrid)
    current, history = sources(hybrid)
    with pytest.raises(DigestValidationError):
        validate_resolution(json.dumps(output([bad])), current, history, hybrid.workflow("global-ps"))


@pytest.mark.parametrize("bad", [
    unresolved(status="confirmed"),
    unresolved(questions=[]),
    unresolved(resolution="Fixed."),
    unresolved(resolution_source_ids=["open"]),
    unresolved(takeaway="Try reinstalling."),
])
def test_invalid_unresolved_rejected(hybrid, bad):
    seed(hybrid)
    current, history = sources(hybrid)
    with pytest.raises(DigestValidationError):
        validate_resolution(json.dumps(output(opened=[bad])), current, history, hybrid.workflow("global-ps"))


def test_partial_and_proposed_remain_unresolved(hybrid):
    seed(hybrid)
    current, history = sources(hybrid)
    result = validate_resolution(json.dumps(output(opened=[
        unresolved(status="partial", source_ids=["partial","reply"], contributors=["Nicola"]),
        unresolved(status="proposed", product="alteon_appwall", source_ids=["ask","proposal"],
                   reported_by=["Artur"], contributors=["Nicola"]),
    ])), current, history, hybrid.workflow("global-ps"))
    assert [i["status"] for i in result["sections"][0]["items"]] == ["partial", "proposed"]
    assert [i["status_label"] for i in result["sections"][0]["items"]] == ["Partially answered", "Suggested approach"]


def test_hybrid_retention_guard(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG.replace("weekly_summary:\n      enabled: true",
                                "weekly_summary:\n      enabled: true\n      evidence_mode: hybrid"))
    with pytest.raises(ConfigError, match="processed_raw_days >= 14"):
        load_config(p)


def test_hybrid_delivery_and_ledger_use_existing_semantics(hybrid):
    seed(hybrid)
    email = FakeDelivery()
    model = FakeGateway(output([item()], [unresolved()]))
    first = run_weekly(hybrid, "global-ps", now=NOW, gateway=model, delivery=email)
    second = run_weekly(hybrid, "global-ps", now=NOW, gateway=model, delivery=email)
    assert first.status == "weekly-smtp-accepted" and second.status == "already-delivered"
    assert len(email.sent) == 1 and model.called == 1
    assert email.sent[0][1]["raw_text"] is None
    assert email.sent[0][0].email["bcc"] == ["test-blind@example.com"]
    with DigestDatabase(hybrid.database_path) as db:
        assert db.workflow_state("global-ps")["checkpoint_seq"] == 8


def test_raw_source_freshness_rechecked_after_model(hybrid):
    seed(hybrid)
    class EditingGateway(FakeGateway):
        def generate_prompt(self, *args, **kwargs):
            response = super().generate_prompt(*args, **kwargs)
            with DigestDatabase(hybrid.database_path) as db:
                db.mark_deleted("confirmed")
            return response
    email = FakeDelivery()
    with pytest.raises(RunnerError, match="raw evidence changed"):
        run_weekly(hybrid, "global-ps", now=NOW, dry_run=True,
                   gateway=EditingGateway(output([item()])), delivery=email)
    assert not email.sent


def test_resolved_does_not_require_redundant_takeaway(hybrid):
    seed(hybrid)
    current, history = sources(hybrid)
    result = validate_resolution(json.dumps(output([item(takeaway="")])), current, history,
                                 hybrid.workflow("global-ps"))
    assert result["sections"][0]["items"][0]["takeaway"] == ""


def test_hybrid_prompt_limit_refuses_instead_of_truncating(hybrid, monkeypatch):
    seed(hybrid)
    monkeypatch.setattr("whatsapp_digest.weekly.MAX_PROMPT_CHARS", 20)
    model = FakeGateway(output([item()]))
    with pytest.raises(RunnerError, match="prompt exceeds safe input limit"):
        run_weekly(hybrid, "global-ps", now=NOW, dry_run=True, gateway=model)
    assert model.called == 0


def test_hybrid_zero_raw_messages_and_zero_cards_render_both_sections(hybrid):
    from datetime import date
    with DigestDatabase(hybrid.database_path) as db:
        seed_artifact(db, "anchor", date(2026, 11, 2), 0, 0, output=digest())
        seed_artifact(db, "monday", date(2026, 11, 9), 0, 0, output=digest())
        db.set_run_message_count("monday", 0)
    model = FakeGateway(output())
    result = run_weekly(hybrid, "global-ps", now=NOW, dry_run=True, gateway=model)
    assert model.called == 0
    assert result.message_count == 0
    assert result.rendered_text.count("None identified in the available evidence.") == 2
    assert "Resolved issues: 0 | Unresolved issues: 0" in result.rendered_text
    assert "<strong>Resolved issues: 0</strong>" in result.rendered_html
    assert "<strong>Unresolved issues: 0</strong>" in result.rendered_html
