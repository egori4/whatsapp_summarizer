from datetime import datetime, timezone
from pathlib import Path

import pytest

from whatsapp_digest.config import load_config
from whatsapp_digest.database import DigestDatabase
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


class FakeGateway:
    def __init__(self, output='{"sections":[]}'):
        self.output = output
        self.calls = []

    def summarize(self, workflow, rows):
        self.calls.append((workflow, list(rows)))
        return ModelInvocationResult(
            raw_output=self.output,
            provider="openai-codex",
            model="gpt-5.6-sol",
            reasoning="medium",
        )


class FailingGateway:
    def summarize(self, workflow, rows):
        raise HermesGatewayError("simulated provider failure")


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def test_uninitialized_workflow_requires_explicit_window(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(RunnerError, match="needs its initial window"):
        run_workflow(cfg, "tests", dry_run=True, now=NOW, gateway=FakeGateway())


def test_first_dry_run_calls_model_once_and_never_initializes_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Upgrade to 10.6.2 resolves issue.")

    gateway = FakeGateway()
    result = run_workflow(cfg, "tests", last="24h", dry_run=True, now=NOW, gateway=gateway)

    assert result.message_count == 1
    assert result.status == "dry-run-no-material-updates"
    assert len(gateway.calls) == 1
    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


def test_model_failure_does_not_move_checkpoint(tmp_path):
    cfg = make_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        seed(db, "m1", "2026-09-27T14:00:00+00:00", "Technical update")

    with pytest.raises(RunnerError, match="model generation failed"):
        run_workflow(cfg, "tests", last="24h", dry_run=True, now=NOW, gateway=FailingGateway())

    with DigestDatabase(cfg.database_path) as db:
        state = db.workflow_state("tests")
        assert state["initialized"] == 0
        assert state["checkpoint_seq"] is None


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


def test_non_dry_run_is_blocked_until_delivery_phase(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(RunnerError, match="Phase 5"):
        run_workflow(cfg, "tests", last="24h", dry_run=False, now=NOW, gateway=FakeGateway())
