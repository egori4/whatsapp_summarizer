from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_SECRET = re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+")


def redact_text(text: str) -> str:
    """Apply the final-model secret redaction idempotently."""
    return _SECRET.sub("[REDACTED]", text)


def model_source_refs(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return opaque, per-request source handles for a model boundary."""
    return [f"S{index:03}" for index in range(1, len(items) + 1)]


def project_model_sources(
    items: Sequence[Mapping[str, Any]], source_refs: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    """Build the only message representation allowed into final-model prompts.

    The projection deliberately excludes raw message and chat identifiers,
    participant metadata, ingest/runtime state, and unredacted text.  Reply
    topology is retained only through opaque source handles.
    """
    refs = list(source_refs) if source_refs is not None else model_source_refs(items)
    if len(refs) != len(items) or len(refs) != len(set(refs)):
        raise ValueError("model source refs must uniquely cover the input")

    by_message_id: dict[str, str] = {}
    duplicate_ids: set[str] = set()
    for item, source_ref in zip(items, refs):
        message_id = str(item.get("message_id", ""))
        if message_id in by_message_id:
            duplicate_ids.add(message_id)
        else:
            by_message_id[message_id] = source_ref

    def linked_refs(item: Mapping[str, Any]) -> list[str]:
        raw_links = [item.get("reply_to"), item.get("quoted_message_id"), *(item.get("referenced_ids") or [])]
        return list(dict.fromkeys(
            source_ref
            for raw_link in raw_links
            if (source_ref := by_message_id.get(str(raw_link))) is not None
            and str(raw_link) not in duplicate_ids
        ))

    projected: list[dict[str, object]] = []
    for item, source_ref in zip(items, refs):
        redacted_text = item.get("redacted_text")
        if not isinstance(redacted_text, str):
            redacted_text = str(item.get("text") or "")
        entry: dict[str, object] = {
            "source_ref": source_ref,
            "text": redact_text(redacted_text),
            "reply_to_refs": linked_refs(item),
        }
        timestamp = item.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            entry["timestamp"] = timestamp
        projected.append(entry)
    return projected


def project_classifier_sources(
    items: Sequence[Mapping[str, Any]], source_refs: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    """Return the strict source-ref/text subset needed by the classifier."""
    return [
        {"source_ref": entry["source_ref"], "text": entry["text"]}
        for entry in project_model_sources(items, source_refs)
    ]
