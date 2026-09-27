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

    def summarize(self, workflow, rows):
        self.calls.append((workflow, list(rows)))
        return ModelInvocationResult(
            raw_output=self.output,
            provider="openai-codex",
            model="gpt-6-sol",
            reasoning="medium",
        )


class FailingGateway:
    def summarize(self, workflow, rows):
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
        def summarize(self, workflow, rows):
            result = super().summarize(workflow, rows)
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
        def summarize(self, workflow, rows):
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
