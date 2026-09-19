from __future__ import annotations

from typing import Any, Sequence

from .actionable_validate import actionable_source_map


def actionable_schema(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    refs = list(actionable_source_map(items))
    ref = {"type": "string"}
    ref_array = {"type": "array", "items": ref, "uniqueItems": True}
    required_ref_array = {**ref_array, "minItems": 1}
    disposition = {
        "type": "object", "additionalProperties": False,
        "required": ["source_ref", "value"],
        "properties": {
            "source_ref": ref,
            "value": {
                "type": "string",
                "enum": ["INCLUDE", "EXCLUDE", "CONTEXT", "UNCERTAIN"],
            },
        },
    }
    specific = {
        "type": "object", "additionalProperties": False,
        "required": ["source_ref", "value"],
        "properties": {"source_ref": ref, "value": {"type": "string", "minLength": 1}},
    }
    topic = {
        "type": "object", "additionalProperties": False,
        "required": [
            "topic", "title", "source_refs", "raw_keep_refs",
            "question", "question_source_ref", "question_source_kind", "resolution_status", "situation", "recommendation", "actions", "specifics", "limitation",
            "reference_refs", "confidence",
        ],
        "properties": {
            "topic": {"type": "string", "minLength": 1, "maxLength": 80},
            "title": {"type": "string", "minLength": 1, "maxLength": 100},
            "source_refs": required_ref_array,
            "raw_keep_refs": required_ref_array,
            "question": {"type": "string", "maxLength": 240},
            "question_source_ref": {"anyOf": [ref, {"type": "null"}]},
            "question_source_kind": {"anyOf": [{"type": "string", "enum": ["QUESTION", "ISSUE", "UPDATE"]}, {"type": "null"}]},
            "resolution_status": {"anyOf": [{"type": "string", "enum": ["partial", "resolved"]}, {"type": "null"}]},
            "situation": {"type": "string", "maxLength": 600},
            "recommendation": {"type": "string", "maxLength": 600},
            "actions": {"type": "array", "maxItems": 12, "items": {"type": "string", "minLength": 1, "maxLength": 600}, "uniqueItems": True},
            "specifics": {"type": "array", "items": specific, "uniqueItems": True},
            "limitation": {"type": "string", "maxLength": 600},
            "reference_refs": ref_array,
            "confidence": {"type": "string", "enum": ["documented", "confirmed", "field_guidance"]},
        },
    }
    unanswered = {
        "type": "object", "additionalProperties": False,
        "required": ["topic", "question_source_ref", "question_source_kind", "context_refs", "reason"],
        "properties": {
            "topic": {"type": "string", "minLength": 1, "maxLength": 80},
            "question_source_ref": ref,
            "question_source_kind": {"type": "string", "enum": ["QUESTION", "ISSUE"]},
            "context_refs": ref_array,
            "reason": {"type": "string", "minLength": 1, "maxLength": 220},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["dispositions", "topics", "unanswered"],
        "properties": {
            "dispositions": {
                "type": "array", "items": disposition,
                "minItems": len(refs), "maxItems": len(refs),
            },
            "topics": {"type": "array", "maxItems": 8, "items": topic},
            "unanswered": {"type": "array", "maxItems": 8, "items": unanswered},
        },
    }
