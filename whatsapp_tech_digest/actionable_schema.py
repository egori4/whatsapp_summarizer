from __future__ import annotations

from typing import Any, Sequence

from .actionable_validate import actionable_source_map


def actionable_schema(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    refs = list(actionable_source_map(items))
    ref = {"type": "string", "enum": refs}
    ref_array = {"type": "array", "items": ref, "uniqueItems": True}
    required_ref_array = {**ref_array, "minItems": 1}
    specific = {
        "type": "object", "additionalProperties": False,
        "required": ["source_ref", "value"],
        "properties": {"source_ref": ref, "value": {"type": "string", "minLength": 1}},
    }
    topic = {
        "type": "object", "additionalProperties": False,
        "required": [
            "topic", "title", "source_refs", "raw_keep_refs", "reason_kept",
            "question", "situation", "recommendation", "specifics", "limitation",
            "reference_refs", "question_refs", "contributor_refs", "confidence",
        ],
        "properties": {
            "topic": {"type": "string", "minLength": 1, "maxLength": 80},
            "title": {"type": "string", "minLength": 1, "maxLength": 100},
            "source_refs": required_ref_array,
            "raw_keep_refs": required_ref_array,
            "reason_kept": {"type": "string", "minLength": 1, "maxLength": 220},
            "question": {"type": "string", "minLength": 1, "maxLength": 240},
            "situation": {"type": "string", "maxLength": 600},
            "recommendation": {"type": "string", "maxLength": 600},
            "specifics": {"type": "array", "items": specific, "uniqueItems": True},
            "limitation": {"type": "string", "maxLength": 600},
            "reference_refs": ref_array,
            "question_refs": ref_array,
            "contributor_refs": ref_array,
            "confidence": {"type": "string", "enum": ["documented", "confirmed", "field_guidance"]},
        },
    }
    unanswered = {
        "type": "object", "additionalProperties": False,
        "required": ["topic", "question_source_ref", "context_refs", "reason"],
        "properties": {
            "topic": {"type": "string", "minLength": 1, "maxLength": 80},
            "question_source_ref": ref,
            "context_refs": ref_array,
            "reason": {"type": "string", "minLength": 1, "maxLength": 220},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["dispositions", "topics", "unanswered"],
        "properties": {
            "dispositions": {
                "type": "object", "additionalProperties": False, "required": refs,
                "properties": {reference: {"type": "string", "enum": ["INCLUDE", "EXCLUDE", "CONTEXT", "UNCERTAIN"]} for reference in refs},
            },
            "topics": {"type": "array", "minItems": 1, "maxItems": 8, "items": topic},
            "unanswered": {"type": "array", "items": unanswered},
        },
    }
