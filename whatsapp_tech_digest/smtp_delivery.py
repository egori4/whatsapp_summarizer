from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from hashlib import sha256
from typing import Protocol

from .config import SmtpPolicy


class SMTPClient(Protocol):
    def starttls(self) -> object: ...
    def login(self, user: str, password: str) -> object: ...
    def send_message(self, message: EmailMessage) -> object: ...
    def quit(self) -> object: ...


class DeliveryError(RuntimeError):
    pass


def message_id(digest_id: str) -> str:
    return f"<{sha256(digest_id.encode('utf-8')).hexdigest()[:32]}@whatsapp-tech-digest.local>"


def build_message(policy: SmtpPolicy, digest_id: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = policy.sender
    message["To"] = policy.recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id(digest_id)
    message.set_content(body, charset="utf-8")
    return message


def send(policy: SmtpPolicy, digest_id: str, subject: str, body: str, smtp_factory=smtplib.SMTP) -> str:
    """Return accepted/failed/unknown; no recipient input and no transport fallback."""
    password = os.environ.get(policy.password_env)
    if not password:
        raise DeliveryError("SMTP secret reference is unavailable")
    client: SMTPClient | None = None
    accepted = False
    try:
        client = smtp_factory(policy.host, policy.port, timeout=policy.timeout_seconds)
        client.starttls()
        client.login(policy.sender, password)
        refused = client.send_message(build_message(policy, digest_id, subject, body))
        # smtplib returns a non-empty mapping for per-recipient refusal.  It is
        # a definite failure, not SMTP acceptance, even when the call returns.
        if refused:
            try:
                client.quit()
            except Exception:
                pass
            return "failed"
        accepted = True
        client.quit()
        return "accepted"
    except Exception as exc:
        if accepted:
            return "unknown"
        raise DeliveryError(f"SMTP transport failed: {type(exc).__name__}") from exc
