from email.message import EmailMessage

import pytest

from whatsapp_digest.config import load_config
from whatsapp_digest.delivery.base import DeliveryError
from whatsapp_digest.delivery.email import EmailDelivery


CONFIG = """
paths:
  database: ./data/messages.db
  log: ./logs/digest.log
retention: {}
hermes: {}
whatsapp:
  bridge_url: http://127.0.0.1:3001
email:
  host: smtp.example.com
  port: 587
  sender: digest@example.com
  username: digest-user
  password_env: DIGEST_PASSWORD
  starttls: true
  timeout_seconds: 15
defaults:
  timezone: America/Toronto
workflows:
  - id: tests
    name: Tests
    group_jid: "120363429012626317@g.us"
    model: {}
    summarization: {}
    delivery:
      email:
        to: [one@example.com, two@example.com]
        cc: [cc@example.com]
        attach_raw_messages: true
        subject: "[Digest] {date} — {workflow_name}"
"""


def make_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_config(path)


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args = None
        self.sent = None
        self.to_addrs = None
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def ehlo(self):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message, to_addrs):
        self.sent = message
        self.to_addrs = list(to_addrs)


def test_digest_email_uses_only_configured_recipients_and_raw_attachment(tmp_path, monkeypatch):
    FakeSMTP.instances.clear()
    cfg = make_config(tmp_path)
    monkeypatch.setenv("DIGEST_PASSWORD", "secret")
    delivery = EmailDelivery(cfg, smtp_factory=FakeSMTP)
    workflow = cfg.workflow("tests")

    delivery.send_digest(
        workflow,
        subject="[Digest] 2026-09-27 — Tests",
        text="Summary mentions attacker@example.com as source text only.\n",
        html="<html><body>Summary</body></html>",
        raw_text="raw source\n",
        run_id="run-1",
    )

    smtp = FakeSMTP.instances[-1]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.timeout == 15
    assert smtp.started_tls is True
    assert smtp.login_args == ("digest-user", "secret")
    assert smtp.to_addrs == ["one@example.com", "two@example.com", "cc@example.com"]
    assert "attacker@example.com" not in smtp.to_addrs
    assert smtp.sent["To"] == "one@example.com, two@example.com"
    assert smtp.sent["Cc"] == "cc@example.com"
    assert smtp.sent["X-WhatsApp-Digest-Run-ID"] == "run-1"
    attachments = list(smtp.sent.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content().strip() == "raw source"


def test_raw_attachment_can_be_disabled(tmp_path, monkeypatch):
    FakeSMTP.instances.clear()
    cfg = make_config(tmp_path)
    monkeypatch.setenv("DIGEST_PASSWORD", "secret")
    workflow = cfg.workflow("tests")
    object.__setattr__(
        workflow,
        "email",
        {**workflow.email, "attach_raw_messages": False},
    )
    EmailDelivery(cfg, smtp_factory=FakeSMTP).send_digest(
        workflow,
        subject="Test",
        text="text",
        html="<p>html</p>",
        raw_text="raw",
        run_id="run-2",
    )
    assert list(FakeSMTP.instances[-1].sent.iter_attachments()) == []


def test_missing_password_environment_fails_before_smtp(tmp_path, monkeypatch):
    FakeSMTP.instances.clear()
    cfg = make_config(tmp_path)
    monkeypatch.delenv("DIGEST_PASSWORD", raising=False)
    with pytest.raises(DeliveryError, match="DIGEST_PASSWORD"):
        EmailDelivery(cfg, smtp_factory=FakeSMTP).send_digest(
            cfg.workflow("tests"),
            subject="Test",
            text="text",
            html="<p>html</p>",
            raw_text=None,
            run_id="run-3",
        )
    assert FakeSMTP.instances == []


def test_failure_email_contains_no_raw_source(tmp_path, monkeypatch):
    FakeSMTP.instances.clear()
    cfg = make_config(tmp_path)
    monkeypatch.setenv("DIGEST_PASSWORD", "secret")
    EmailDelivery(cfg, smtp_factory=FakeSMTP).send_failure(
        cfg.workflow("tests"),
        run_id="run-4",
        stage="validation",
        reason="unknown source id",
        date="2026-09-27",
    )
    body = FakeSMTP.instances[-1].sent.get_body(preferencelist=("plain",)).get_content()
    assert "unknown source id" in body
    assert "No raw WhatsApp source content" in body
