from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .models import ModelFailure


_PROTECTED_FACT = re.compile(
    r"https?://[^\s]+"
    r"|(?<!\w)/(?:[A-Za-z0-9_.<>-]+/)*(?:[A-Za-z0-9_.<>-]+)(?:\s+<[^>\n]+>)?"
    r"|\b\d+(?:\.\d+){1,4}\b"
    r"|\b\d{5,}\b"
)
_LOW_CONFIDENCE = re.compile(r"(?i)\b(?:i think|i believe|probably|if i recall|not sure|maybe)\b")
_IDENTITY_LABEL = re.compile(r"^\+?\d[\d\s().-]{5,}$")


def actionable_source_map(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Short deterministic model-facing refs mapped to immutable local rows."""
    return {f"S{index:03d}": item for index, item in enumerate(items, start=1)}


def source_text(item: Mapping[str, Any]) -> str:
    return str(item.get("redacted_text") or item.get("text") or "")


def protected_values(text: str) -> set[str]:
    return {match.group(0).rstrip(".,;)") for match in _PROTECTED_FACT.finditer(text)}


def clean_field(value: object, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ModelFailure(f"{field} must be text")
    cleaned = " ".join(value.split())
    if required and not cleaned:
        raise ModelFailure(f"{field} must be nonempty")
    return cleaned


def names(refs: Sequence[str], sources: Mapping[str, Mapping[str, Any]]) -> list[str]:
    names: list[str] = []
    for reference in refs:
        raw_name = sources[reference].get("display_name")
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name or _IDENTITY_LABEL.fullmatch(name):
            continue
        if "\n" in name:
            raise ModelFailure("participant metadata invalid")
        if name not in names:
            names.append(name)
    return names


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def date_label(items: Sequence[dict[str, Any]]) -> str:
    dates = []
    for item in items:
        parsed = parse_time(item.get("timestamp"))
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo("America/Toronto"))
            dates.append(parsed.date())
    if not dates:
        return "Undated"
    first, last = min(dates), max(dates)
    if first == last:
        return f"{first.strftime('%b')} {first.day}"
    if first.month == last.month and first.year == last.year:
        return f"{first.strftime('%b')} {first.day}–{last.day}"
    return f"{first.strftime('%b')} {first.day}–{last.strftime('%b')} {last.day}"


def low_confidence(text: str) -> bool:
    return _LOW_CONFIDENCE.search(text) is not None
