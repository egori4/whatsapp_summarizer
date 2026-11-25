from dataclasses import replace
import sqlite3

import pytest

from whatsapp_digest.database import DigestDatabase
from whatsapp_digest.resend import resend_daily
from whatsapp_digest.runner import RunnerError, run_workflow
from whatsapp_digest.cli import main
from test_v2_weekly_integration import fixture_config, daily_message, init_checkpoint, DailyGateway
from test_v2_weekly import NOW, FakeDelivery


def setup_digest(tmp_path):
    cfg = fixture_config(tmp_path)
    workflow = cfg.workflow("global-ps")
    cfg = replace(cfg, workflows=tuple(
        replace(w, email={**w.email, "attach_raw_messages": True,
                         "cc": ["cc@example.com"], "bcc": ["bcc@example.com"]})
        if w.id == workflow.id else w for w in cfg.workflows))
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    result = run_workflow(cfg, "global-ps", now=NOW, gateway=DailyGateway(), delivery=FakeDelivery())
    return cfg, result


def snapshot(cfg):
    with sqlite3.connect(cfg.database_path) as conn:
        return {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("runs", "workflow_state", "daily_digest_artifacts", "weekly_reports", "messages")}


def test_resend_saved_digest_only_primary_and_no_ledger_changes(tmp_path):
    cfg, original = setup_digest(tmp_path)
    before = snapshot(cfg)
    channel = FakeDelivery()
    preview = resend_daily(cfg, original.run_id, dry_run=True, delivery=channel)
    assert preview.status == "dry-run" and not channel.sent
    result = resend_daily(cfg, original.run_id, delivery=channel)
    workflow, msg = channel.sent[0]
    assert result.status == "smtp-accepted"
    assert result.recipients == tuple(cfg.workflow("global-ps").email["to"])
    assert workflow.email["cc"] == [] and workflow.email["bcc"] == []
    assert msg["run_id"] == original.run_id
    assert "Finding" in msg["html"]
    assert "Reconstructed" in msg["raw_text"]
    assert result.source_count == 1
    assert snapshot(cfg) == before
    resend_daily(cfg, original.run_id, all_recipients=True, without_raw=True, delivery=channel)
    workflow, msg = channel.sent[-1]
    assert workflow.email["cc"] == ["cc@example.com"] and workflow.email["bcc"] == ["bcc@example.com"]
    assert msg["raw_text"] is None
    assert snapshot(cfg) == before


def test_resend_missing_raw_and_unknown_id(tmp_path):
    cfg, original = setup_digest(tmp_path)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.execute("DELETE FROM messages")
    channel = FakeDelivery()
    with pytest.raises(RunnerError, match="without-raw"):
        resend_daily(cfg, original.run_id, delivery=channel)
    with pytest.raises(RunnerError, match="No retained"):
        resend_daily(cfg, "missing", delivery=channel)
    assert not channel.sent
    resend_daily(cfg, original.run_id, without_raw=True, delivery=channel)
    assert channel.sent[0][1]["raw_text"] is None


def test_resend_cli_dry_run(tmp_path, capsys):
    cfg, original = setup_digest(tmp_path)
    assert main(["--config", str(cfg.source_path), "resend", original.run_id, "--dry-run", "--without-raw"]) == 0
    assert "inbox delivery unverified" in capsys.readouterr().out
