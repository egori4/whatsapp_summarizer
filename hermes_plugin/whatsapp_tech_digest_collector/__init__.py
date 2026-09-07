"""Hermes collector-only hook for the WhatsApp Tech Digest.

This plugin has no tools, outbound platform actions, scheduler, model, or SMTP
capabilities. It only appends an approved group event to the durable local spool
and returns the pre-dispatch ``skip`` directive.
"""

from __future__ import annotations

import json
import logging
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKIP = {"action": "skip", "reason": "whatsapp_tech_digest_collected"}
_SKIP_FAILURE = {"action": "skip", "reason": "whatsapp_tech_digest_collection_failed"}
_ALLOW = {"action": "allow", "reason": "non_target"}


def _required_absolute_path(ctx: Any, key: str, *, kind: str) -> Path:
    value = ctx.get_config(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing required plugin setting {key!r}")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"plugin setting {key!r} must be absolute")
    resolved = path.resolve(strict=True)
    if kind == "dir" and not resolved.is_dir():
        raise ValueError(f"plugin setting {key!r} must name a directory")
    if kind == "file" and not resolved.is_file():
        raise ValueError(f"plugin setting {key!r} must name a file")
    return resolved


def _require_owner_only(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError(f"protected path is not owner-only: {path}")


def _to_utc_timestamp(event: Any) -> str:
    raw = getattr(event, "raw_message", None)
    raw_timestamp = raw.get("timestamp") if isinstance(raw, dict) else None
    if isinstance(raw_timestamp, (int, float)) and not isinstance(raw_timestamp, bool):
        return datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).isoformat()
    event_timestamp = getattr(event, "timestamp", None)
    if not isinstance(event_timestamp, datetime):
        raise ValueError("event lacks a bridge or event timestamp")
    if event_timestamp.tzinfo is None:
        event_timestamp = event_timestamp.replace(tzinfo=timezone.utc)
    return event_timestamp.astimezone(timezone.utc).isoformat()


def _spool_record(event: Any, target_jid: str) -> dict[str, Any]:
    source = getattr(event, "source", None)
    chat_jid = getattr(source, "chat_id", None)
    participant = getattr(event, "user_id", None) or getattr(source, "user_id", None)
    display_name = getattr(event, "user_name", None) or getattr(source, "user_name", None)
    message_id = getattr(event, "message_id", None)
    if chat_jid != target_jid:
        raise ValueError("non-target event cannot be converted into a spool record")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("target event lacks an immutable message ID")
    if not isinstance(participant, str) or not participant:
        raise ValueError("target event lacks a participant identity")
    text = getattr(event, "text", "")
    if not isinstance(text, str):
        raise ValueError("target event text must be a string")
    return {
        "chat_jid": chat_jid,
        "message_id": message_id,
        "participant": participant,
        "display_name": display_name if isinstance(display_name, str) else None,
        "timestamp": _to_utc_timestamp(event),
        "text": text,
    }


def register(ctx: Any) -> None:
    project_root = _required_absolute_path(ctx, "project_root", kind="dir")
    policy_path = _required_absolute_path(ctx, "policy_path", kind="file")
    _require_owner_only(policy_path)
    package = project_root / "whatsapp_tech_digest"
    if not package.is_dir():
        raise ValueError("project_root does not contain whatsapp_tech_digest")
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from whatsapp_tech_digest.config import DigestConfig
    from whatsapp_tech_digest.spool import DurableSpool

    policy = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    policy.require_live_safe()
    state_dir = Path(policy.paths["state_dir"]).resolve()
    spool_path = Path(policy.paths["spool"]).resolve()
    if spool_path.parent != state_dir:
        raise ValueError("spool path must be directly inside policy state_dir")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_owner_only(state_dir)
    # Create and validate the schema during registration, but do not retain the
    # thread-affine connection. Hook invocations create and close their own
    # handle below.
    with DurableSpool(spool_path, policy.target_group_jid):
        pass

    def pre_gateway_dispatch(*, event: Any, **_kwargs: Any) -> dict[str, str]:
        source = getattr(event, "source", None)
        if getattr(source, "chat_id", None) != policy.target_group_jid:
            return _ALLOW
        try:
            # Hermes may invoke this hook from a different worker thread than
            # plugin registration. SQLite connections are thread-affine by
            # default, so open and close the durable handle in the invoking
            # thread instead of retaining a registration-thread connection.
            with DurableSpool(spool_path, policy.target_group_jid) as spool:
                spool.append_message(_spool_record(event, policy.target_group_jid))
        except Exception as exc:
            # Do not include the event, message content, participant, or JID in
            # failure telemetry: a target-event ingestion failure is consumed.
            logger.warning("WhatsApp Tech Digest collector ingestion failed: %s", type(exc).__name__)
            return _SKIP_FAILURE
        logger.info("WhatsApp Tech Digest collector spooled target event")
        return _SKIP

    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
