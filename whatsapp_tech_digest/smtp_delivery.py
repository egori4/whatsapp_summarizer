from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from hashlib import sha256
from html import escape
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


_LABEL = re.compile(r"^(Question asked|Summary|Action / follow-up|Commands|Key details|Limitation|Reference|Asked by|Contributors):\s*(.*)$")


def _inline_html(text: str) -> str:
    return escape(text, quote=True)


def _digest_html(markdown: str) -> str:
    """Render the digest's constrained Markdown subset to a readable HTML email."""
    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items = []

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush_list()
            continue
        if line.startswith("# "):
            flush_list()
            blocks.append(f"<h1>{_inline_html(line[2:])}</h1>")
        elif line.startswith("## "):
            flush_list()
            blocks.append(f"<h2>{_inline_html(line[3:])}</h2>")
        elif line.startswith("### "):
            flush_list()
            blocks.append(f"<h3>{_inline_html(line[4:])}</h3>")
        elif line.startswith(("- ", "* ")):
            list_items.append(f"<li>{_inline_html(line[2:])}</li>")
        else:
            flush_list()
            labeled_line = line
            if labeled_line.startswith("**") and ":**" in labeled_line:
                label_end = labeled_line.index(":**")
                labeled_line = labeled_line[2:label_end + 1] + labeled_line[label_end + 3:]
            match = _LABEL.fullmatch(labeled_line)
            if match:
                label, value = match.groups()
                blocks.append(f"<p class=\"field\"><strong>{_inline_html(label)}:</strong> {_inline_html(value)}</p>")
            else:
                blocks.append(f"<p>{_inline_html(line)}</p>")
    flush_list()
    return """<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<style>
body { margin:0; padding:0; background:#f4f7fb; color:#182230; font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Arial,sans-serif; line-height:1.55; }
main { max-width:760px; margin:0 auto; padding:24px 14px 36px; }
article { background:#fff; border:1px solid #dce3ec; border-radius:14px; padding:24px; box-shadow:0 1px 3px rgba(15,23,42,.08); }
h1 { margin:0 0 22px; color:#0f2742; font-size:24px; line-height:1.25; }
h2 { margin:26px 0 10px; padding-top:18px; border-top:1px solid #e3e8ef; color:#143d65; font-size:19px; line-height:1.3; }
h2:first-of-type { border-top:0; padding-top:0; }
h3 { margin:18px 0 8px; color:#1e4d7a; font-size:16px; }
p { margin:9px 0; }
.field { padding-left:12px; border-left:3px solid #93c5fd; }
strong { color:#0f2742; }
ul { margin:8px 0 14px 22px; padding:0; }
li { margin:5px 0; }
@media (max-width:520px) { main { padding:8px; } article { padding:16px; border-radius:10px; } h1 { font-size:21px; } h2 { font-size:17px; } }
</style></head><body><main><article>""" + "\n".join(blocks) + "</article></main></body></html>"


def build_message(policy: SmtpPolicy, digest_id: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = policy.sender
    message["To"] = policy.recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id(digest_id)
    message.set_content(body, charset="utf-8", cte="quoted-printable")
    message.add_alternative(_digest_html(body), subtype="html")
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
