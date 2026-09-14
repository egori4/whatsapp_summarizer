from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .model_projection import contains_opaque_identifier, sanitize_opaque_identifiers
from .models import ModelFailure


_PROTECTED_URL = re.compile(r"https?://[^\s]+")
_PROTECTED_PATH = re.compile(
    r"(?<![:/\w])/(?:[A-Za-z0-9_.<>-]+/)*(?:[A-Za-z0-9_.<>-]+)(?:\s+<[^>\n]+>)?"
)
_PROTECTED_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_+-])[A-Za-z0-9](?:[A-Za-z0-9_+.-]*[A-Za-z0-9+])?(?![A-Za-z0-9_+-])"
)
_LOW_CONFIDENCE = re.compile(
    r"(?i)\b(?:i think|i believe|i guess|probably|possibly|perhaps|maybe|might|seems?|seemed|"
    r"apparently|afaik|iirc|if i recall|not sure|unsure|unconfirmed|not confirmed)\b"
)
_IDENTITY_LABEL = re.compile(r"^\+?\d[\d\s().-]{5,}$")
_MENTION_PLACEHOLDER = re.compile(r"\[mentioned participant\]", re.IGNORECASE)
_PARTICIPANT_PLACEHOLDER = re.compile(r"\[participant identifier\]", re.IGNORECASE)
# Deliberately over-inclusive: a false positive only forces the model to keep a message.
_STATUS_CHANGE = re.compile(
    r"(?i)\b(?:cancell?ed|cancell?ing|cancels|called off|reschedul\w*|schedul\w*|confirm\w*|"
    r"postpon\w*|deferr\w*|delay\w*|moved|pushed back|brought forward|no longer (?:happening|taking place))\b"
)


def actionable_source_map(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Short deterministic model-facing refs mapped to immutable local rows."""
    return {f"S{index:03d}": item for index, item in enumerate(items, start=1)}


def source_text(item: Mapping[str, Any]) -> str:
    return str(item.get("redacted_text") or item.get("text") or "")


def announces_status_change(text: str) -> bool:
    """Detect a declarative, unhedged statement that something was scheduled, moved, or called off.

    This is a recall net, not a classifier: its only consequence is that the model must
    account for the message rather than drop it silently.
    """
    return any(
        "?" not in sentence and not low_confidence(sentence) and _STATUS_CHANGE.search(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    )


def _trim_url_punctuation(value: str) -> str:
    value = value.rstrip(".,;")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def protected_values(text: str) -> set[str]:
    """Extract exact source tokens whose spelling must never be synthesized.

    This intentionally covers short and punctuation-bearing identifiers such as
    ``v2``, ``3PAR``, ``SRX_345``, ``802.11ax``, and ``C++17`` in addition to
    URLs, commands, versions, and long numeric identifiers.
    """
    values: set[str] = set()
    occupied: list[tuple[int, int]] = []
    for match in (*_PROTECTED_URL.finditer(text), *_PROTECTED_PATH.finditer(text)):
        value = match.group(0)
        value = _trim_url_punctuation(value) if value.startswith(("http://", "https://")) else value.rstrip(".,;)")
        if value:
            values.add(value)
            occupied.append((match.start(), match.end()))
    for match in _PROTECTED_TOKEN.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        value = match.group(0).rstrip(".,;)")
        has_alpha = any(character.isalpha() for character in value)
        has_digit = any(character.isdigit() for character in value)
        is_version = bool(re.fullmatch(r"\d+(?:\.\d+)+", value))
        if (has_alpha and has_digit) or is_version or (value.isdigit() and len(value) >= 5):
            values.add(value)
    return values


def protected_value_occurs(value: str, text: str) -> bool:
    """Require an exact protected token occurrence, never a substring of another token."""
    return re.search(
        r"(?<![A-Za-z0-9_+-])"
        + re.escape(value)
        + r"(?!(?:[A-Za-z0-9_+-]|\.[A-Za-z0-9]))",
        text,
    ) is not None


def clean_field(value: object, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ModelFailure(f"{field} must be text")
    cleaned = " ".join(value.split())
    if required and not cleaned:
        raise ModelFailure(f"{field} must be nonempty")
    return cleaned


def clean_reader_field(value: object, field: str, *, required: bool = False) -> str:
    """Normalize a model field that will be emitted to the reader."""
    cleaned = clean_field(value, field, required=required)
    if (
        contains_opaque_identifier(cleaned)
        or _MENTION_PLACEHOLDER.search(cleaned)
        or _PARTICIPANT_PLACEHOLDER.search(cleaned)
    ):
        raise ModelFailure("reader-facing identity placeholder")
    return cleaned


def grounded_reader_text(value: str, evidence: str | Sequence[str]) -> bool:
    """Return whether reader prose is an exact normalized source excerpt."""
    normalized_value = " ".join(value.split())
    evidence_items = (evidence,) if isinstance(evidence, str) else evidence
    pattern = re.compile(r"(?<!\w)" + re.escape(normalized_value) + r"(?!\w)")
    for item in evidence_items:
        normalized_evidence = " ".join(item.split())
        for match in pattern.finditer(normalized_evidence):
            prefix = normalized_evidence[:match.start()]
            if re.search(r"(?i)\b(?:not|never|don't|don’t|avoid)\s*$", prefix):
                continue
            return True
    return not normalized_value


def require_grounded_reader_text(value: str, evidence: str | Sequence[str], field: str) -> None:
    """Require substantive reader prose to be an exact normalized source excerpt."""
    if not grounded_reader_text(value, evidence):
        raise ModelFailure(f"unsupported {field}: not an exact source excerpt")


def safe_reader_source_text(value: object, field: str) -> str:
    """Make source text safe when it must be quoted in reader-facing output."""
    cleaned = clean_field(value, field, required=True)
    cleaned = sanitize_opaque_identifiers(cleaned, "a participant")
    cleaned = _MENTION_PLACEHOLDER.sub("a participant", cleaned)
    return _PARTICIPANT_PLACEHOLDER.sub("a participant", cleaned)


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
    carried_window_dates = []
    for item in items:
        if item.get("carried_forward") is True:
            for value in item.get("digest_window_timestamps") or []:
                parsed = parse_time(value)
                if parsed is not None:
                    if parsed.tzinfo is not None:
                        parsed = parsed.astimezone(ZoneInfo("America/Toronto"))
                    carried_window_dates.append(parsed.date())
            continue
        parsed = parse_time(item.get("timestamp"))
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo("America/Toronto"))
            dates.append(parsed.date())
    if not dates:
        dates = carried_window_dates
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
