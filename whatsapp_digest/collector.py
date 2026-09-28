"""Read-only collector for the upstream Hermes WhatsApp bridge.

The collector only performs GET requests against the loopback bridge. It never
calls bridge send/edit/media/typing endpoints and WhatsApp content never enters
Hermes agent dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable
from urllib.request import urlopen

from .config import AppConfig
from .database import DigestDatabase

logger = logging.getLogger(__name__)


@dataclass
class CollectorStats:
    received: int = 0
    stored: int = 0
    updated: int = 0
    unchanged: int = 0
    discovered_groups: int = 0
    ignored_direct: int = 0
    ignored_media_without_caption: int = 0
    invalid: int = 0


class BridgeReadError(RuntimeError):
    pass


def _timestamp_iso(value: Any) -> str:
    if isinstance(value, bool):
        value = None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return datetime.fromtimestamp(float(stripped), tz=timezone.utc).isoformat()
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def _is_placeholder_media(event: dict[str, Any], body: str) -> bool:
    if not event.get("hasMedia"):
        return False
    media_type = str(event.get("mediaType") or "").strip()
    return bool(media_type and body.strip() == f"[{media_type} received]")


class WhatsAppCollector:
    def __init__(
        self,
        config: AppConfig,
        database: DigestDatabase,
        *,
        opener: Callable[..., Any] = urlopen,
    ):
        self.config = config
        self.database = database
        self._opener = opener
        self._configured_jids = {workflow.group_jid for workflow in config.workflows}
        self._messages_url = config.whatsapp["bridge_url"] + "/messages"

    def fetch_messages(self) -> list[dict[str, Any]]:
        try:
            with self._opener(self._messages_url, timeout=10) as response:
                payload = response.read()
        except Exception as exc:
            raise BridgeReadError(f"failed to read WhatsApp bridge: {type(exc).__name__}") from exc
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeReadError("WhatsApp bridge returned invalid JSON") from exc
        if not isinstance(parsed, list):
            raise BridgeReadError("WhatsApp bridge /messages response must be a list")
        return [item for item in parsed if isinstance(item, dict)]

    def process_batch(self, events: list[dict[str, Any]]) -> CollectorStats:
        stats = CollectorStats(received=len(events))
        for event in events:
            is_group = bool(event.get("isGroup"))
            chat_id = str(event.get("chatId") or "").strip()

            # WhatsApp is not a conversational channel. DMs/status/non-group
            # events are discarded completely and never persisted.
            if not is_group or not chat_id.endswith("@g.us"):
                stats.ignored_direct += 1
                continue

            if chat_id not in self._configured_jids:
                # Discovery stores group metadata only. Never persist source
                # text, participant identity, caption, or quoted content.
                display_name = event.get("chatName")
                if not isinstance(display_name, str) or not display_name.strip():
                    display_name = None
                self.database.discover_group(
                    chat_id,
                    display_name.strip() if isinstance(display_name, str) else None,
                    seen_at=_timestamp_iso(event.get("timestamp")),
                )
                stats.discovered_groups += 1
                continue

            message_id = event.get("messageId")
            if not isinstance(message_id, str) or not message_id.strip():
                stats.invalid += 1
                continue

            body = event.get("body", "")
            if not isinstance(body, str):
                stats.invalid += 1
                continue

            if _is_placeholder_media(event, body):
                # Phase 1 supports text/captions only. A media-only placeholder
                # carries no source material worth summarizing.
                stats.ignored_media_without_caption += 1
                continue

            sender_id = event.get("senderId")
            sender_name = event.get("senderName")
            sender_id = sender_id if isinstance(sender_id, str) and sender_id else None
            sender_name = sender_name if isinstance(sender_name, str) and sender_name else None
            has_media = bool(event.get("hasMedia"))
            text = None if has_media else body
            caption = body if has_media else None

            action, _seq = self.database.upsert_message(
                message_id=message_id.strip(),
                group_jid=chat_id,
                sender_id=sender_id,
                display_name=sender_name,
                occurred_at=_timestamp_iso(event.get("timestamp")),
                text=text,
                caption=caption,
            )
            if action == "inserted":
                stats.stored += 1
            elif action == "updated":
                stats.updated += 1
            else:
                stats.unchanged += 1
        return stats

    def collect_once(self) -> CollectorStats:
        return self.process_batch(self.fetch_messages())

    def run_forever(self, *, stop: Callable[[], bool] | None = None) -> None:
        """Poll continuously.

        A fetched batch is retained in memory until it has been processed
        successfully. On a transient persistence failure, no new bridge batch
        is fetched until the current batch succeeds.
        """
        pending: list[dict[str, Any]] | None = None
        interval = float(self.config.whatsapp["poll_interval_seconds"])
        while not (stop and stop()):
            try:
                if pending is None:
                    pending = self.fetch_messages()
                if pending:
                    stats = self.process_batch(pending)
                    logger.info(
                        "WhatsApp collector batch received=%d stored=%d updated=%d "
                        "unchanged=%d discovered_groups=%d ignored_direct=%d "
                        "ignored_media_without_caption=%d invalid=%d",
                        stats.received,
                        stats.stored,
                        stats.updated,
                        stats.unchanged,
                        stats.discovered_groups,
                        stats.ignored_direct,
                        stats.ignored_media_without_caption,
                        stats.invalid,
                    )
                pending = None
            except BridgeReadError as exc:
                logger.warning("WhatsApp bridge read failed: %s", exc)
            except Exception as exc:
                logger.error("WhatsApp collector persistence failed: %s", type(exc).__name__)
                # Keep pending in memory for retry; do not fetch another batch.
            time.sleep(interval)
