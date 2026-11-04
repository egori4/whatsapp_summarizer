"""Daily artifact, schedule, and provider integration for optional weekly reporting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path

import pytest

from whatsapp_digest.cli import main
from whatsapp_digest.config import load_config
from whatsapp_digest.database import DigestDatabase
from whatsapp_digest.delivery.base import DeliveryError
from whatsapp_digest.delivery.email import EmailDelivery
from whatsapp_digest.model.hermes import HermesGatewayError, ModelInvocationResult
from whatsapp_digest.runner import RunnerError, run_workflow
from whatsapp_digest.systemd import install_schedules, render_units, schedule_status
from whatsapp_digest.weekly import run_weekly

from test_v2_weekly import CONFIG as WEEKLY_CONFIG, NOW, FakeDelivery, FakeGateway, seed_week


def fixture_config(tmp_path, *, enabled=True):
    p=tmp_path/"config.yaml"
    s=WEEKLY_CONFIG
    if not enabled:
        s=s.replace("enabled: true", "enabled: false")
    p.write_text(s)
    return load_config(p)


def daily_message(db, message_id="wa-message-id", text="A technical update"):
    db.upsert_message(
        message_id=message_id, group_jid="120363429012626317@g.us",
        sender_id=None, display_name="Artur",
        occurred_at="2026-11-04T12:05:00+00:00", text=text,
    )


class DailyGateway:
    def __init__(self, raw=None, fail=False):
        self.raw = raw if raw is not None else json.dumps({
            "sections": [{"section_id": "technical_updates", "title": "Technical Updates",
                          "items": [{
                              "title": "Finding", "summary": "Issue found",
                              "details": [], "actions": [], "questions": [],
                              "reported_by": ["Artur"], "contributors": [],
                              "references": [], "source_ids": ["wa-message-id"],
                          }]}],
        })
        self.fail = fail
        self.called = 0

    def summarize(self, workflow, rows, *, context_rows=None):
        self.called += 1
        if self.fail:
            raise HermesGatewayError("failed")
        return ModelInvocationResult(
            raw_output=self.raw, provider="fake", model="unit-test", reasoning="none",
        )


def init_checkpoint(db):
    db.set_checkpoint("global-ps", 0)


def test_daily_real_run_captures_validated_artifact_atomically(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    email=FakeDelivery()
    result=run_workflow(cfg, "global-ps", now=NOW,
                        gateway=DailyGateway(), delivery=email)
    assert result.status == "delivered"
    assert len(email.sent) == 1
    with DigestDatabase(cfg.database_path) as db:
        artifacts=db._conn.execute("SELECT * FROM daily_digest_artifacts").fetchall()
        assert len(artifacts)==1
        assert artifacts[0]["run_id"]==result.run_id
        assert artifacts[0]["checkpoint_before"]==0
        assert artifacts[0]["checkpoint_after"]==1
        assert json.loads(artifacts[0]["digest_json"])==result.digest
        assert db.workflow_state("global-ps")["checkpoint_seq"]==1


@pytest.mark.parametrize("kind",["no-pending","no-usable","no-material"])
def test_daily_empty_successes_persist_empty_artifact(tmp_path,kind):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        if kind=="no-usable":
            daily_message(db, text="placeholder")
            db.mark_deleted("wa-message-id")
        elif kind=="no-material":
            daily_message(db)
    output='{"sections":[]}'
    result=run_workflow(cfg,"global-ps",now=NOW,
                        gateway=DailyGateway(output),delivery=FakeDelivery())
    assert result.status=="success-"+kind
    with DigestDatabase(cfg.database_path) as db:
        rows=db._conn.execute("SELECT * FROM daily_digest_artifacts").fetchall()
        assert len(rows)==1
        assert json.loads(rows[0]["digest_json"])=={"sections":[]}
        assert db.workflow_state("global-ps")["checkpoint_seq"]==result.cutoff_seq


def test_daily_disabled_does_not_capture_artifacts(tmp_path):
    cfg=fixture_config(tmp_path,enabled=False)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    r=run_workflow(cfg, "global-ps", now=NOW,
                   gateway=DailyGateway(),delivery=FakeDelivery())
    assert r.status=="delivered"
    with DigestDatabase(cfg.database_path) as db:
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_daily_dryrun_never_persists_or_advances(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    r=run_workflow(cfg,"global-ps",now=NOW,dry_run=True,
                   gateway=DailyGateway(),delivery=FakeDelivery())
    assert r.status=="dry-run-success"
    with DigestDatabase(cfg.database_path) as db:
        assert db.workflow_state("global-ps")["checkpoint_seq"]==0
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_daily_explicit_replay_not_canonical(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    r=run_workflow(cfg,"global-ps",last="7d",now=NOW,
                   gateway=DailyGateway(),delivery=FakeDelivery())
    assert r.status=="delivered"
    with DigestDatabase(cfg.database_path) as db:
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_daily_delivery_failure_no_artifact(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    with pytest.raises(RunnerError,match="email delivery failed"):
        run_workflow(cfg,"global-ps",now=NOW,gateway=DailyGateway(),
                     delivery=FakeDelivery(fail=DeliveryError("no")))
    with DigestDatabase(cfg.database_path) as db:
        assert db.workflow_state("global-ps")["checkpoint_seq"]==0
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_daily_model_failure_no_artifact(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        daily_message(db)
    with pytest.raises(RunnerError,match="model generation failed"):
        run_workflow(cfg,"global-ps",now=NOW,gateway=DailyGateway(fail=True),
                     delivery=FakeDelivery())
    with DigestDatabase(cfg.database_path) as db:
        assert db.workflow_state("global-ps")["checkpoint_seq"]==0
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_atomic_finalize_rolls_back_checkpoint_and_artifact(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        db.create_run(run_id="capture",workflow_id="global-ps",
                      mode="scheduled",checkpoint_before=0,cutoff_seq=1)
        db._conn.executescript("""
            CREATE TRIGGER reject_artifact BEFORE INSERT ON daily_digest_artifacts
            BEGIN SELECT RAISE(ABORT,'forced-error'); END;
        """)
        with pytest.raises(sqlite3.DatabaseError):
            db.complete_success_and_checkpoint(
                "capture","global-ps",1,status="delivered",
                digest_artifact={"sections": []},checkpoint_before=0,
            )
        assert db.workflow_state("global-ps")["checkpoint_seq"]==0
        assert db._conn.execute(
            "SELECT status FROM runs WHERE run_id='capture'"
        ).fetchone()[0]=="running"
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_repeated_finalize_idempotent_and_cannot_rewind(tmp_path):
    cfg=fixture_config(tmp_path)
    with DigestDatabase(cfg.database_path) as db:
        init_checkpoint(db)
        db.create_run(run_id="r1",workflow_id="global-ps",
                      mode="scheduled",checkpoint_before=0,cutoff_seq=1)
        args=dict(digest_artifact={"sections": []},checkpoint_before=0)
        db.complete_success_and_checkpoint("r1","global-ps",1,status="delivered",**args)
        db.set_checkpoint("global-ps",9)
        db.complete_success_and_checkpoint("r1","global-ps",1,status="delivered",**args)
        assert db.workflow_state("global-ps")["checkpoint_seq"]==9
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==1


def test_old_v24_schema_migrates_in_place(tmp_path):
    cfg=fixture_config(tmp_path)
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(cfg.database_path) as conn:
        conn.executescript("""
          CREATE TABLE metadata(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
          INSERT INTO metadata VALUES('change_seq',42);
          CREATE TABLE workflow_state(
            workflow_id TEXT PRIMARY KEY, checkpoint_seq INTEGER, initialized INTEGER NOT NULL,
            last_success_at TEXT, last_run_id TEXT, last_run_status TEXT);
          INSERT INTO workflow_state VALUES('legacy',20,1,NULL,NULL,NULL);
        """)
    with DigestDatabase(cfg.database_path) as db:
        assert db.latest_seq()==42
        assert db.workflow_state("legacy")["checkpoint_seq"]==20
        assert db._conn.execute("SELECT COUNT(*) FROM daily_digest_artifacts").fetchone()[0]==0


def test_weekly_units_and_install_cleanup(tmp_path):
    cfg=fixture_config(tmp_path)
    unit=render_units(cfg,cfg.workflow("global-ps"),python_executable="/opt/venv/bin/python",weekly=True)
    assert unit.service_name=="whatsapp-digest-global-ps-weekly.service"
    assert 'weekly "global-ps" --scheduled' in unit.service_text
    assert unit.on_calendar=="Mon *-*-* 08:30:00 America/Toronto"
    import subprocess
    calls=[]
    def fake(cmd,**kw):
        calls.append(cmd)
        if cmd[:3]==["systemctl","--user","is-active"]:
            return subprocess.CompletedProcess(cmd,0,"active\n","")
        if cmd[:3]==["systemctl","--user","is-enabled"]:
            return subprocess.CompletedProcess(cmd,0,"enabled\n","")
        if "NextElapseUSecRealtime" in str(cmd):
            return subprocess.CompletedProcess(cmd,0,"Monday 08:30\n","")
        return subprocess.CompletedProcess(cmd,0,"","")
    unit_dir=tmp_path/"units"
    installed=install_schedules(cfg,unit_dir=unit_dir,executor=fake)
    assert len(installed)==2
    daily_text=(unit_dir/"whatsapp-digest-global-ps.service").read_text()
    assert 'run "global-ps" --scheduled' in daily_text
    assert (unit_dir/"whatsapp-digest-global-ps-weekly.timer").exists()
    disabled=fixture_config(tmp_path,enabled=False)
    installed=install_schedules(disabled,unit_dir=unit_dir,executor=fake)
    assert len(installed)==1
    assert (unit_dir/"whatsapp-digest-global-ps.timer").exists()
    assert not (unit_dir/"whatsapp-digest-global-ps-weekly.timer").exists()
    assert ["systemctl","--user","disable","--now","whatsapp-digest-global-ps-weekly.timer"] in calls


def test_status_shows_weekly_enabled_and_disabled(tmp_path,capsys):
    cfg=fixture_config(tmp_path)
    assert main(["--config",str(cfg.source_path),"status","global-ps"])==0
    assert "Weekly: enabled" in capsys.readouterr().out
    cfg=fixture_config(tmp_path,enabled=False)
    assert main(["--config",str(cfg.source_path),"status","global-ps"])==0
    assert "Weekly: disabled" in capsys.readouterr().out


def test_weekly_selects_correct_provider_path(monkeypatch,tmp_path):
    import whatsapp_digest.weekly as w
    for is_ollama in (False,True):
        path=tmp_path/"config.yaml"
        content=WEEKLY_CONFIG
        if is_ollama:
            content=content.replace("provider: inherit","provider: ollama-local").replace(
                "name: inherit","name: qwen3.5:9b"
            ).replace("reasoning: high","reasoning: none")
        path.write_text(content)
        cfg=load_config(path)
        # New, isolated DB for each provider case.
        if cfg.database_path.exists():
            cfg.database_path.unlink()
        seed_week(cfg)
        selected=FakeGateway()
        if is_ollama:
            monkeypatch.setattr(w,"OllamaStructuredGateway",lambda config: selected)
            monkeypatch.setattr(w,"HermesModelGateway",
                                lambda config: (_ for _ in ()).throw(AssertionError("cloud fallback")))
        else:
            monkeypatch.setattr(w,"HermesModelGateway",lambda config: selected)
        result=run_weekly(cfg,"global-ps",now=NOW,dry_run=True,delivery=FakeDelivery())
        assert result.status=="weekly-dry-run-success"
        assert selected.called==1


def test_weekly_smtp_bcc_envelope_not_visible_headers(tmp_path):
    cfg=fixture_config(tmp_path)
    seed_week(cfg)
    seen=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def ehlo(self):
            pass
        def starttls(self):
            pass
        def send_message(self,message,to_addrs):
            seen.append((message,list(to_addrs)))
            return {}
    transport=EmailDelivery(cfg,smtp_factory=FakeSMTP)
    result=run_weekly(cfg,"global-ps",now=NOW,
                      gateway=FakeGateway(),delivery=transport)
    assert result.status=="weekly-delivered"
    assert len(seen)==1
    message,recipients=seen[0]
    assert recipients==["test-recipient@example.com","test-blind@example.com"]
    assert "Bcc" not in message
    assert not any(part.get_filename() for part in message.walk())
    assert all("david.medina" not in addr.lower() for addr in recipients)
