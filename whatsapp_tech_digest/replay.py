"""Offline replay of a WhatsApp text export into the same durable spool boundary.

This module intentionally bypasses Baileys and the gateway.  It parses a local
text export and constructs deterministic synthetic events before calling
``DurableSpool.append_message`` — the exact SQLite ingestion boundary used by
the collector.  Exported sender identifiers are pseudonymized before storage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .spool import DurableSpool


class ReplayFormatError(ValueError):
    """The supplied text is not a supported WhatsApp text export."""


_HEADER = re.compile(r"^\[(?P<stamp>[^\]]+)\]\s+(?P<sender>[^:]+):\s?(?P<text>.*)$")
_TIMESTAMP_FORMATS = ("%I:%M %p, %m/%d/%Y", "%m/%d/%Y %I:%M %p")
_PHONE_LABEL = re.compile(r"^\+?[0-9][0-9 ()-]*$")


@dataclass(frozen=True)
class _ExportMessage:
    ordinal: int
    stamp: str
    sender: str
    text: str


def _parse_timestamp(value: str, timezone_name: str) -> str:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ReplayFormatError(f"unknown replay timezone: {timezone_name}") from exc
    for timestamp_format in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(value, timestamp_format).replace(tzinfo=timezone).astimezone(ZoneInfo("UTC")).isoformat()
        except ValueError:
            continue
    raise ReplayFormatError(f"unsupported WhatsApp timestamp: {value!r}")


def _parse_export(exported: str) -> list[_ExportMessage]:
    if not isinstance(exported, str) or not exported.strip():
        raise ReplayFormatError("WhatsApp export must be nonempty text")
    messages: list[_ExportMessage] = []
    current: _ExportMessage | None = None
    for line in exported.splitlines():
        match = _HEADER.match(line)
        if match:
            if current is not None:
                messages.append(current)
            current = _ExportMessage(len(messages) + 1, match.group("stamp").strip(), match.group("sender").strip(), match.group("text"))
        elif current is None:
            if line.strip():
                raise ReplayFormatError("text before the first WhatsApp message header")
        else:
            current = _ExportMessage(current.ordinal, current.stamp, current.sender, current.text + "\n" + line)
    if current is not None:
        messages.append(current)
    if not messages:
        raise ReplayFormatError("WhatsApp export did not contain any message headers")
    return messages


def _pseudonym(value: str, *, prefix: str, length: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}{digest}"


def _event(message: _ExportMessage, target_group_jid: str, timezone_name: str) -> dict[str, Any]:
    canonical = "\x00".join(("whatsapp-export-v1", str(message.ordinal), message.stamp, message.sender, message.text))
    message_id = _pseudonym(canonical, prefix="replay-v1-", length=24)
    participant = _pseudonym(message.sender, prefix="replay-", length=24)
    display_name = message.sender if not _PHONE_LABEL.fullmatch(message.sender) else _pseudonym(message.sender, prefix="participant-", length=8)
    return {
        "chat_jid": target_group_jid,
        "message_id": message_id,
        "participant": participant,
        "display_name": display_name,
        "timestamp": _parse_timestamp(message.stamp, timezone_name),
        "text": message.text,
    }


def replay_whatsapp_export(exported: str, spool: DurableSpool, *, timezone_name: str) -> list[dict[str, Any]]:
    """Parse an exported chat and append deterministic synthetic events to ``spool``.

    The function is idempotent for an unchanged export and target spool: event IDs
    derive from the source row position, timestamp, sender, and body.  It never
    uses a WhatsApp session, bridge, network call, or outbound messaging path.
    """
    messages = _parse_export(exported)
    events = [_event(message, spool.target_group_jid, timezone_name) for message in messages]
    for event in events:
        spool.append_message(event)
    return events
