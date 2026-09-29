from datetime import datetime, timezone
from pathlib import Path

import pytest

from whatsapp_digest.config import load_config
from whatsapp_digest.database import DigestDatabase
from whatsapp_digest.delivery.base import DeliveryError
from whatsapp_digest.model.hermes import HermesGatewayError, ModelInvocationResult
from whatsapp_digest.runner import RunnerError, run_workflow


CONFIG = """
paths:
  database: ./data/messages.db
  log: ./logs/digest.log
retention:
  processed_raw_days: 7
  log_days: 30
hermes:
  command: hermes
  timeout_seconds: 60
whatsapp:
  bridge_url: http://127.0.0.1:3001
  poll_interval_seconds: 1
email:
  host: smtp.example.com
  port: 587
  sender: digest@example.com
  password_env: DIGEST_PASSWORD
defaults:
  timezone: America/Toronto
workflows:
  - id: tests
    name: Tests
    group_jid: "120363429012626317@g.us"
    schedule:
      type: daily
      at: "08:00"
    model:
      provider: inherit
      name: inherit
      reasoning: inherit
    summarization:
      instructions: Technical updates.
      sections:
        - id: technical
          title: Technical Updates
          guidance: Technical material.
    delivery:
      email:
        to: [user@example.com]
        attach_raw_messages: true
        subject: "[Digest] {date} — {workflow_name}"
"""


def make_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_config(path)


def seed(db, message_id, when, text):
    return db.upsert_message(
        message_id=message_id,
        group_jid="120363429012626317@g.us",
        sender_id=None,
        display_name="Rahul",
        occurred_at=when,
        text=text,
    )[1]


def material_output():
    return (
        '{"sections":[{"section_id":"technical","title":"Technical Updates","items":['
        '{"title":"Update","summary":"Technical update.","details":[],"actions":[],"questions":[],'
        '"reported_by":["Rahul"],"contributors":[],"references":[],"source_ids":["m1"]}]}]}'
    )


class FakeGateway:
    def __init__(self, output='{"sections":[]}'):
        self.output = output
        self.calls = []

    def summarize(self, workflow, rows, *, context_rows=None):
        self.calls.append((workflow, list(rows), list(context_rows or [])))
        return ModelInvocationResult(
            raw_output=self.output,
            provider="openai-codex",
            model="gpt-6-sol",
            reasoning="medium",
        )


class FailingGateway:
    def summarize(self, workflow, rows, *, context_rows=None):
        raise HermesGatewayError("simulated provider failure")


class FakeDelivery:
    def __init__(self, fail_digest=False):
        self.fail_digest = fail_digest
        self.digests = []
        self.failures = []

    def send_digest(self, workflow, **kwargs):
        if self.fail_digest:
            raise DeliveryError("simulated SMTP failure")
        self.digests.append((workflow.id, kwargs))

    def send_failure(self, workflow, **kwargs):
        self.failures.append((workflow.id, kwargs))


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def test_uninitialized_workflow_requires_explicit_window(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(RunnerError, match="needs its initial window"):
        run_workflow(cfg, "tests", dry_run=True, now=NOW, gateway=FakeGateway())


def test_first_dry_run_calls_model_once_and_never_initializes_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Upgrade to 10.6.2 resolves issue.")

    delivery = FakeDelivery()
    gateway = FakeGateway()
    result = run_workflow(
        cfg, "tests", last="24h", dry_run=True, now=NOW,
        gateway=gateway, delivery=delivery,
    )

    assert result.message_count == 1
    assert result.status == "dry-run-no-material-updates"
    assert len(gateway.calls) == 1
    assert delivery.digests == []
    assert delivery.failures == []
    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


def test_model_failure_does_not_move_checkpoint_and_real_run_notifies(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    delivery = FakeDelivery()
    with pytest.raises(RunnerError, match="model generation failed"):
        run_workflow(
            cfg, "tests", last="24h", dry_run=False, now=NOW,
            gateway=FailingGateway(), delivery=delivery,
        )

    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None
    assert len(delivery.failures) == 1
    assert delivery.failures[0][1]["stage"] == "model"


def test_validation_failure_does_not_move_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    invalid = '{"sections":[{"section_id":"not-configured","title":"Bad","items":[]}]}'
    with pytest.raises(RunnerError, match="validation failed"):
        run_workflow(cfg, "tests", last="24h", dry_run=True, now=NOW, gateway=FakeGateway(invalid))

    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


def test_validation_failure_can_capture_raw_model_output(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    invalid = "not valid digest JSON"
    monkeypatch.setenv("WHATSAPP_DIGEST_DEBUG_MODEL_OUTPUT", "1")

    with pytest.raises(RunnerError, match="validation failed"):
        run_workflow(cfg, "tests", last="24h", dry_run=True, now=NOW, gateway=FakeGateway(invalid))

    debug_files = list((cfg.database_path.parent / "debug-model-output").glob("*.txt"))
    assert len(debug_files) == 1
    assert debug_files[0].read_text(encoding="utf-8") == invalid
    assert debug_files[0].stat().st_mode & 0o777 == 0o600


def test_debug_capture_failure_does_not_mask_validation_failure(tmp_path, monkeypatch):
    import whatsapp_digest.runner as runner_module

    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    invalid = "not valid digest JSON"
    monkeypatch.setenv("WHATSAPP_DIGEST_DEBUG_MODEL_OUTPUT", "1")
    real_os_open = runner_module.os.open

    def fail_debug_open(path, flags, mode=0o777):
        if "debug-model-output" in str(path):
            raise OSError("disk unavailable")
        return real_os_open(path, flags, mode)

    monkeypatch.setattr(runner_module.os, "open", fail_debug_open)

    with pytest.raises(RunnerError, match="validation failed"):
        run_workflow(cfg, "tests", last="24h", dry_run=True, now=NOW, gateway=FakeGateway(invalid))

    with DigestDatabase(cfg.database_path) as db:
        row = db._conn.execute("SELECT status, failure_stage FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        assert row["status"] == "failed"
        assert row["failure_stage"] == "validation"


def test_initialized_run_uses_only_changes_after_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        first = seed(db, "m1", "2026-09-27T13:00:00+00:00", "Old update")
        db.set_checkpoint("tests", first, run_id="previous")
        seed(db, "m2", "2026-09-27T14:00:00+00:00", "New update")

    gateway = FakeGateway()
    result = run_workflow(cfg, "tests", dry_run=True, now=NOW, gateway=gateway)

    assert result.message_count == 1
    assert [row["message_id"] for row in gateway.calls[0][1]] == ["m2"]
    with DigestDatabase(cfg.database_path) as db:
        assert db.workflow_state("tests")["checkpoint_seq"] == first


def test_last_window_excludes_older_messages(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "old", "2026-09-25T14:00:00+00:00", "Old")
        seed(db, "recent", "2026-09-27T14:00:00+00:00", "Recent")

    gateway = FakeGateway()
    result = run_workflow(cfg, "tests", last="24h", dry_run=True, now=NOW, gateway=gateway)
    assert result.message_count == 1
    assert [row["message_id"] for row in gateway.calls[0][1]] == ["recent"]


def test_successful_delivery_advances_exactly_to_cutoff(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        cutoff = seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    delivery = FakeDelivery()
    result = run_workflow(
        cfg, "tests", last="24h", dry_run=False, now=NOW,
        gateway=FakeGateway(material_output()), delivery=delivery,
    )

    assert result.status == "delivered"
    assert len(delivery.digests) == 1
    assert delivery.digests[0][1]["subject"] == "[Digest] 2026-09-27 — Tests"
    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 1
        assert state["checkpoint_seq"] == cutoff
        assert state["last_run_status"] == "delivered"


def test_delivery_failure_never_advances_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    with pytest.raises(RunnerError, match="email delivery failed"):
        run_workflow(
            cfg, "tests", last="24h", dry_run=False, now=NOW,
            gateway=FakeGateway(material_output()), delivery=FakeDelivery(fail_digest=True),
        )

    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


def test_valid_no_material_result_advances_without_email(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        cutoff = seed(db, "m1", "2026-09-27T14:00:00+00:00", "Only chatter")

    delivery = FakeDelivery()
    result = run_workflow(
        cfg, "tests", last="24h", dry_run=False, now=NOW,
        gateway=FakeGateway('{"sections":[]}'), delivery=delivery,
    )

    assert result.status == "success-no-material"
    assert delivery.digests == []
    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["checkpoint_seq"] == cutoff
        assert state["initialized"] == 1


def test_freshness_change_blocks_delivery_and_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    class MutatingGateway(FakeGateway):
        def summarize(self, workflow, rows, *, context_rows=None):
            result = super().summarize(workflow, rows, context_rows=context_rows)
            with DigestDatabase(cfg.database_path) as other:
                other.upsert_message(
                    message_id="m1",
                    group_jid="120363429012626317@g.us",
                    sender_id=None,
                    display_name="Rahul",
                    occurred_at="2026-09-27T14:00:00+00:00",
                    text="Edited while model was running",
                )
            return result

    delivery = FakeDelivery()
    with pytest.raises(RunnerError, match="changed during the run"):
        run_workflow(
            cfg, "tests", last="24h", dry_run=False, now=NOW,
            gateway=MutatingGateway(material_output()), delivery=delivery,
        )

    assert delivery.digests == []
    assert len(delivery.failures) == 1
    assert delivery.failures[0][1]["stage"] == "freshness"
    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


def test_raw_export_uses_gateway_source_records_not_reread_database(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Database text")

    class ExactGateway:
        def summarize(self, workflow, rows, *, context_rows=None):
            return ModelInvocationResult(
                raw_output='{"sections":[]}',
                provider="openai-codex",
                model="gpt-6-sol",
                reasoning="medium",
                source_records=(
                    {
                        "source_id": "m1",
                        "timestamp": "2026-09-27T14:00:00+00:00",
                        "sender": "Olesya",
                        "text": "Exact text supplied to Hermes",
                    },
                ),
            )

    result = run_workflow(
        cfg,
        "tests",
        last="24h",
        dry_run=True,
        now=NOW,
        gateway=ExactGateway(),
    )

    assert "Exact text supplied to Hermes" in result.raw_text
    assert "Database text" not in result.raw_text
    assert "Provider: openai-codex" in result.rendered_text
    assert "Model: gpt-6-sol" in result.rendered_text


def test_scheduled_uninitialized_workflow_is_skipped_cleanly(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Pending before initialization")

    gateway = FakeGateway(material_output())
    delivery = FakeDelivery()
    result = run_workflow(
        cfg,
        "tests",
        scheduled=True,
        now=NOW,
        gateway=gateway,
        delivery=delivery,
    )

    assert result.status == "needs-initial-run"
    assert gateway.calls == []
    assert delivery.digests == []
    assert delivery.failures == []
    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


def test_runner_blocks_concurrent_same_workflow(tmp_path):
    from whatsapp_digest.locking import workflow_lock

    cfg = make_config(tmp_path)
    with workflow_lock(cfg.database_path, "tests"):
        with pytest.raises(RunnerError, match="already running"):
            run_workflow(
                cfg,
                "tests",
                last="24h",
                dry_run=True,
                now=NOW,
                gateway=FakeGateway(),
                delivery=FakeDelivery(),
            )


def test_model_receives_messages_in_chronological_order(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "later", "2026-09-27T14:10:00+00:00", "Later")
        seed(db, "earlier", "2026-09-27T14:00:00+00:00", "Earlier")

    gateway = FakeGateway()
    run_workflow(
        cfg,
        "tests",
        last="24h",
        dry_run=True,
        now=NOW,
        gateway=gateway,
    )

    assert [row["message_id"] for row in gateway.calls[0][1]] == ["earlier", "later"]


def test_successful_real_run_applies_processed_message_retention(tmp_path):
    cfg = make_config(tmp_path)
    old_time = "2026-08-01T12:00:00+00:00"
    with DigestDatabase(cfg.database_path) as db:
        db.upsert_message(
            message_id="old",
            group_jid="120363429012626317@g.us",
            sender_id=None,
            display_name="Rahul",
            occurred_at=old_time,
            text="Old collected message",
            changed_at=old_time,
            collected_at=old_time,
        )

    result = run_workflow(
        cfg,
        "tests",
        last="1h",
        dry_run=False,
        now=NOW,
        gateway=FakeGateway(),
        delivery=FakeDelivery(),
    )

    assert result.status == "success-no-pending"
    with DigestDatabase(cfg.database_path) as db:
        assert db.get_messages(["old"]) == []


def test_initialized_run_supplies_recent_processed_context_without_counting_it(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        old_seq = seed(db, "old1", "2026-09-27T13:00:00+00:00", "Move the 5400S to 35.0.2?")
        db.set_checkpoint("tests", old_seq, run_id="previous")
        seed(db, "m2", "2026-09-27T14:00:00+00:00", "Yes!")

    output = (
        '{"sections":[{"section_id":"technical","title":"Technical Updates","items":['
        '{"title":"5400S version confirmed","summary":"Move to 35.0.2.","details":[],"actions":[],"questions":[],'
        '"reported_by":[],"contributors":[],"references":[],"source_ids":["old1","m2"]}]}]}'
    )
    gateway = FakeGateway(output)
    result = run_workflow(cfg, "tests", dry_run=True, now=NOW, gateway=gateway)
    assert result.message_count == 1
    assert [row["message_id"] for row in gateway.calls[0][1]] == ["m2"]
    assert [row["message_id"] for row in gateway.calls[0][2]] == ["old1"]
    assert "Context: 1 prior messages (48h lookback)" in result.rendered_text
    assert "HISTORICAL CONTEXT" in result.raw_text


def test_explicit_window_does_not_add_checkpoint_context(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        old_seq = seed(db, "old1", "2026-09-27T13:00:00+00:00", "Old")
        db.set_checkpoint("tests", old_seq, run_id="previous")
        seed(db, "m2", "2026-09-27T14:00:00+00:00", "New")
    gateway = FakeGateway()
    run_workflow(cfg, "tests", last="2h", dry_run=True, now=NOW, gateway=gateway)
    assert gateway.calls[0][2] == []
