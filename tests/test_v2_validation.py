import json

import pytest

from whatsapp_digest.config import SectionConfig, WorkflowConfig
from whatsapp_digest.validation import DigestValidationError, validate_digest


def workflow(sections=True):
    configured = (SectionConfig("technical", "Technical Updates", ""),) if sections else ()
    return WorkflowConfig(
        id="tests",
        name="Tests",
        group_jid="120363429012626317@g.us",
        timezone="America/Toronto",
        schedule=None,
        model={"provider": "inherit", "name": "inherit", "reasoning": "inherit"},
        instructions="Technical updates.",
        sections=configured,
        email={"to": ["user@example.com"], "cc": [], "attach_raw_messages": True, "subject": "Test"},
    )


SOURCES = [
    {
        "message_id": "m1",
        "occurred_at": "2026-09-27T12:00:00+00:00",
        "display_name": "Rahul",
        "text": "Upgrade to 10.6.2 resolves the issue. https://kb.example/123",
        "caption": None,
    },
    {
        "message_id": "m2",
        "occurred_at": "2026-09-27T12:05:00+00:00",
        "display_name": "David",
        "text": "Please validate with the customer.",
        "caption": None,
    },
]


def valid_payload():
    return {
        "sections": [{
            "section_id": "technical",
            "title": "Model title is ignored for configured sections",
            "items": [{
                "title": "Upgrade resolves issue",
                "summary": "The issue was reported resolved after upgrade to 10.6.2.",
                "details": ["Upgrade to 10.6.2 was reported as the resolution."],
                "actions": ["Validate with the customer."],
                "questions": [],
                "reported_by": ["Rahul"],
                "contributors": ["David"],
                "references": ["https://kb.example/123"],
                "source_ids": ["m1", "m2"],
            }],
        }],
    }


def test_valid_digest_uses_configured_section_title():
    result = validate_digest(json.dumps(valid_payload()), workflow(), SOURCES)
    assert result["sections"][0]["title"] == "Technical Updates"


def test_empty_configured_sections_are_valid_and_omitted():
    payload = {"sections": [{"section_id": "technical", "title": "Technical Updates", "items": []}]}
    assert validate_digest(json.dumps(payload), workflow(), SOURCES) == {"sections": []}


def test_completely_empty_digest_is_valid():
    assert validate_digest('{"sections":[]}', workflow(), SOURCES) == {"sections": []}


def test_unknown_section_is_rejected():
    payload = valid_payload()
    payload["sections"][0]["section_id"] = "invented"
    with pytest.raises(DigestValidationError, match="not configured"):
        validate_digest(json.dumps(payload), workflow(), SOURCES)


def test_unknown_source_id_is_rejected():
    payload = valid_payload()
    payload["sections"][0]["items"][0]["source_ids"] = ["missing"]
    with pytest.raises(DigestValidationError, match="unknown source"):
        validate_digest(json.dumps(payload), workflow(), SOURCES)


def test_invented_participant_attribution_is_rejected():
    payload = valid_payload()
    payload["sections"][0]["items"][0]["reported_by"] = ["Alice"]
    with pytest.raises(DigestValidationError, match="unknown participant"):
        validate_digest(json.dumps(payload), workflow(), SOURCES)


def test_invented_url_is_rejected():
    payload = valid_payload()
    payload["sections"][0]["items"][0]["references"] = ["https://evil.example/fake"]
    with pytest.raises(DigestValidationError, match="URL not present"):
        validate_digest(json.dumps(payload), workflow(), SOURCES)


def test_unconfigured_sections_may_be_model_created():
    payload = valid_payload()
    payload["sections"][0]["section_id"] = "product_update"
    payload["sections"][0]["title"] = "Product Update"
    result = validate_digest(json.dumps(payload), workflow(sections=False), SOURCES)
    assert result["sections"][0]["section_id"] == "product_update"
    assert result["sections"][0]["title"] == "Product Update"


def test_single_json_code_fence_is_tolerated():
    raw = "```json\n" + json.dumps(valid_payload()) + "\n```"
    assert validate_digest(raw, workflow(), SOURCES)["sections"]
