"""Structural validation for one-call digest model output."""
from __future__ import annotations

import json
import re
from typing import Any

from .config import WorkflowConfig
from .model.hermes import MAX_OUTPUT_CHARS, safe_display_name


class DigestValidationError(ValueError):
    pass


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def _string(value: Any, name: str, *, max_chars: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise DigestValidationError(f"{name} must be a string")
    result = value.strip()
    if required and not result:
        raise DigestValidationError(f"{name} must not be empty")
    if len(result) > max_chars:
        raise DigestValidationError(f"{name} exceeds {max_chars} characters")
    return result


def _string_list(value: Any, name: str, *, max_items: int = 50, max_chars: int = 4000) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DigestValidationError(f"{name} must be a list")
    if len(value) > max_items:
        raise DigestValidationError(f"{name} exceeds {max_items} items")
    return [_string(item, f"{name}[{index}]", max_chars=max_chars) for index, item in enumerate(value)]


def _parse(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise DigestValidationError("model output is empty")
    if len(raw) > MAX_OUTPUT_CHARS:
        raise DigestValidationError("model output exceeds safe size")
    text = raw.strip()
    match = _FENCE_RE.fullmatch(text)
    if match:
        text = match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DigestValidationError(f"model output is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise DigestValidationError("model output must be a JSON object")
    return parsed


def validate_digest(
    raw: str,
    workflow: WorkflowConfig,
    source_rows: list[Any],
    *,
    context_rows: list[Any] | None = None,
) -> dict[str, Any]:
    parsed = _parse(raw)
    raw_sections = parsed.get("sections")
    if not isinstance(raw_sections, list):
        raise DigestValidationError("sections must be a list")
    if len(raw_sections) > 30:
        raise DigestValidationError("sections exceeds 30 entries")

    context_rows = context_rows or []
    all_rows = [*context_rows, *source_rows]
    source_by_id = {str(row["message_id"]): row for row in all_rows}
    allowed_ids = set(source_by_id)
    current_ids = {str(row["message_id"]) for row in source_rows}
    allowed_names = {
        name
        for row in all_rows
        if (name := safe_display_name(row["display_name"])) != "Unknown participant"
    }
    configured_sections = {section.id: section for section in workflow.sections}

    normalized_sections: list[dict[str, Any]] = []
    for section_index, section_value in enumerate(raw_sections):
        section_name = f"sections[{section_index}]"
        if not isinstance(section_value, dict):
            raise DigestValidationError(f"{section_name} must be an object")
        section_id = _string(section_value.get("section_id"), f"{section_name}.section_id", max_chars=100)

        if configured_sections:
            if section_id not in configured_sections:
                raise DigestValidationError(f"{section_name}.section_id is not configured: {section_id}")
            title = configured_sections[section_id].title
        else:
            title = _string(section_value.get("title"), f"{section_name}.title", max_chars=200)

        raw_items = section_value.get("items", [])
        if not isinstance(raw_items, list):
            raise DigestValidationError(f"{section_name}.items must be a list")
        if len(raw_items) > 100:
            raise DigestValidationError(f"{section_name}.items exceeds 100 entries")

        items: list[dict[str, Any]] = []
        for item_index, item_value in enumerate(raw_items):
            item_name = f"{section_name}.items[{item_index}]"
            if not isinstance(item_value, dict):
                raise DigestValidationError(f"{item_name} must be an object")

            source_ids = _string_list(item_value.get("source_ids"), f"{item_name}.source_ids", max_items=100, max_chars=200)
            if not source_ids:
                raise DigestValidationError(f"{item_name}.source_ids must contain at least one source")
            unknown = [source_id for source_id in source_ids if source_id not in allowed_ids]
            if unknown:
                raise DigestValidationError(f"{item_name}.source_ids references unknown source: {unknown[0]}")

            if not any(source_id in current_ids for source_id in source_ids):
                raise DigestValidationError(f"{item_name}.source_ids must reference at least one current source")

            reported_by = _string_list(item_value.get("reported_by"), f"{item_name}.reported_by", max_items=20, max_chars=120)
            contributors = _string_list(item_value.get("contributors"), f"{item_name}.contributors", max_items=30, max_chars=120)
            for person in reported_by + contributors:
                if person not in allowed_names:
                    raise DigestValidationError(f"{item_name} attributes unknown participant: {person}")

            references = _string_list(item_value.get("references"), f"{item_name}.references", max_items=30, max_chars=2000)
            referenced_text = "\n".join(
                str(source_by_id[source_id]["text"] or source_by_id[source_id]["caption"] or "")
                for source_id in source_ids
            )
            for reference in references:
                if reference.lower().startswith(("http://", "https://")) and reference not in referenced_text:
                    raise DigestValidationError(f"{item_name} contains URL not present in cited sources")

            items.append({
                "title": _string(item_value.get("title"), f"{item_name}.title", max_chars=240),
                "summary": _string(item_value.get("summary"), f"{item_name}.summary", max_chars=5000),
                "details": _string_list(item_value.get("details"), f"{item_name}.details"),
                "actions": _string_list(item_value.get("actions"), f"{item_name}.actions"),
                "questions": _string_list(item_value.get("questions"), f"{item_name}.questions"),
                "reported_by": reported_by,
                "contributors": contributors,
                "references": references,
                "source_ids": source_ids,
            })

        # Empty sections are explicitly valid and simply omitted.
        if items:
            normalized_sections.append({
                "section_id": section_id,
                "title": title,
                "items": items,
            })

    return {"sections": normalized_sections}
