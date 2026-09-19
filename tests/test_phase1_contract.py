from __future__ import annotations

import copy
import json
import unittest
from typing import Callable

from whatsapp_tech_digest import actionable_render
from whatsapp_tech_digest.actionable_schema import actionable_schema
from whatsapp_tech_digest.actionable_validate import actionable_source_map
from whatsapp_tech_digest.model_projection import project_model_sources
from whatsapp_tech_digest.model_provider import structured_output_contract
from whatsapp_tech_digest.models import ModelFailure


class PhaseOneContractTests(unittest.TestCase):
    @staticmethod
    def measurement_fixture(count: int) -> list[dict[str, object]]:
        return [
            {
                "message_id": f"synthetic-{index}",
                "change_seq": index,
                "timestamp": "2026-09-01T12:00:00+00:00",
                "text": (
                    f"Synthetic engineering update {index}: release R{index}.1 "
                    f"uses /synthetic/check/{index}."
                ),
            }
            for index in range(1, count + 1)
        ]

    def sources(self) -> list[dict[str, object]]:
        return [
            {
                "message_id": "synthetic-question",
                "change_seq": 1,
                "timestamp": "2026-09-01T12:00:00+00:00",
                "text": "Which command verifies the synthetic service?",
            },
            {
                "message_id": "synthetic-answer",
                "change_seq": 2,
                "timestamp": "2026-09-01T12:01:00+00:00",
                "text": "Run /synthetic/service/check to verify the synthetic service.",
            },
            {
                "message_id": "synthetic-reference",
                "change_seq": 3,
                "timestamp": "2026-09-01T12:02:00+00:00",
                "text": "Reference: https://docs.example.invalid/synthetic-service",
            },
        ]

    def response(self) -> dict[str, object]:
        return {
            "dispositions": [
                {"source_ref": "S001", "value": "INCLUDE"},
                {"source_ref": "S002", "value": "INCLUDE"},
                {"source_ref": "S003", "value": "INCLUDE"},
            ],
            "topics": [{
                "topic": "Synthetic service verification",
                "title": "Synthetic service verification",
                "source_refs": ["S001", "S002", "S003"],
                "raw_keep_refs": ["S001", "S002", "S003"],
                "question": "Which command verifies the synthetic service?",
                "question_source_ref": "S001",
                "question_source_kind": "QUESTION",
                "resolution_status": "resolved",
                "situation": "",
                "recommendation": "Run /synthetic/service/check to verify the synthetic service.",
                "actions": [],
                "specifics": [{"source_ref": "S002", "value": "/synthetic/service/check"}],
                "limitation": "",
                "reference_refs": ["S003"],
                "confidence": "documented",
            }],
            "unanswered": [],
        }

    def render(self, response: dict[str, object]) -> str:
        return actionable_render.render_actionable(json.dumps(response), self.sources())

    def test_schema_uses_list_form_dispositions_without_source_enums(self) -> None:
        schema = actionable_schema(self.sources())
        dispositions = schema["properties"]["dispositions"]

        self.assertEqual(dispositions["type"], "array")
        self.assertEqual(
            dispositions["items"]["required"],
            ["source_ref", "value"],
        )
        self.assertEqual(dispositions["items"]["properties"]["source_ref"], {"type": "string"})
        serialized = json.dumps(schema, separators=(",", ":"))
        self.assertNotIn('"enum":["S001","S002","S003"]', serialized)

    def test_list_dispositions_require_exact_source_set(self) -> None:
        self.assertIn("Synthetic service verification", self.render(self.response()))

        cases = {
            "missing": [
                {"source_ref": "S001", "value": "INCLUDE"},
                {"source_ref": "S002", "value": "INCLUDE"},
            ],
            "duplicate": [
                {"source_ref": "S001", "value": "INCLUDE"},
                {"source_ref": "S001", "value": "EXCLUDE"},
                {"source_ref": "S002", "value": "INCLUDE"},
                {"source_ref": "S003", "value": "INCLUDE"},
            ],
            "unknown": [
                {"source_ref": "S001", "value": "INCLUDE"},
                {"source_ref": "S002", "value": "INCLUDE"},
                {"source_ref": "S999", "value": "INCLUDE"},
            ],
        }
        for label, dispositions in cases.items():
            with self.subTest(label=label):
                response = self.response()
                response["dispositions"] = dispositions
                with self.assertRaisesRegex(ModelFailure, "source disposition"):
                    self.render(response)

        for row in (
            {"value": "INCLUDE"},
            {"source_ref": "S001"},
            {"source_ref": "S001", "value": "INCLUDE", "extra": True},
        ):
            with self.subTest(row=row):
                response = self.response()
                response["dispositions"] = [row]
                with self.assertRaisesRegex(ModelFailure, "source disposition row"):
                    self.render(response)

    def test_nested_reference_arrays_reject_unknown_and_duplicate_refs(self) -> None:
        for field in ("source_refs", "raw_keep_refs", "reference_refs"):
            for label, value in (
                ("unknown", ["S999"]),
                ("duplicate", ["S001", "S001"]),
            ):
                with self.subTest(field=field, label=label):
                    response = self.response()
                    response["topics"][0][field] = value
                    with self.assertRaisesRegex(ModelFailure, field):
                        self.render(response)

        for label, value in (
            ("unknown", ["S999"]),
            ("duplicate", ["S002", "S002"]),
        ):
            with self.subTest(field="context_refs", label=label):
                response = self.response()
                response["topics"] = []
                response["unanswered"] = [{
                    "topic": "Synthetic question",
                    "question_source_ref": "S001",
                    "question_source_kind": "QUESTION",
                    "context_refs": value,
                    "reason": "No reusable answer was established.",
                }]
                with self.assertRaisesRegex(ModelFailure, "unanswered refs"):
                    self.render(response)

    def test_nested_scalar_reference_fields_reject_unknown_or_missing_refs(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, object]], object]]] = [
            (
                "question_source_ref unknown",
                lambda response: response["topics"][0].update(question_source_ref="S999"),
            ),
            (
                "specific source_ref unknown",
                lambda response: response["topics"][0]["specifics"][0].update(source_ref="S999"),
            ),
            (
                "specific source_ref missing",
                lambda response: response["topics"][0]["specifics"][0].pop("source_ref"),
            ),
            (
                "unanswered question_source_ref unknown",
                self._replace_with_unknown_unanswered,
            ),
            (
                "unanswered question_source_ref missing",
                self._replace_with_missing_unanswered,
            ),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                response = copy.deepcopy(self.response())
                mutate(response)
                with self.assertRaises(ModelFailure):
                    self.render(response)

    def test_nested_reference_containers_reject_missing_fields(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, object]], object]]] = [
            (
                field,
                lambda response, field=field: response["topics"][0].pop(field),
            )
            for field in (
                "source_refs", "raw_keep_refs", "question_source_ref",
                "specifics", "reference_refs",
            )
        ]
        cases.extend((
            (
                "unanswered question_source_ref",
                self._replace_with_missing_unanswered,
            ),
            (
                "unanswered context_refs",
                self._replace_with_missing_unanswered_context,
            ),
        ))
        for label, mutate in cases:
            with self.subTest(label=label):
                response = copy.deepcopy(self.response())
                mutate(response)
                with self.assertRaisesRegex(ModelFailure, "schema drift"):
                    self.render(response)

    @staticmethod
    def _replace_with_unknown_unanswered(response: dict[str, object]) -> None:
        response["topics"] = []
        response["unanswered"] = [{
            "topic": "Synthetic question",
            "question_source_ref": "S999",
            "question_source_kind": "QUESTION",
            "context_refs": [],
            "reason": "No reusable answer was established.",
        }]

    @staticmethod
    def _replace_with_missing_unanswered(response: dict[str, object]) -> None:
        response["topics"] = []
        response["unanswered"] = [{
            "topic": "Synthetic question",
            "question_source_kind": "QUESTION",
            "context_refs": [],
            "reason": "No reusable answer was established.",
        }]

    @staticmethod
    def _replace_with_missing_unanswered_context(response: dict[str, object]) -> None:
        response["topics"] = []
        response["unanswered"] = [{
            "topic": "Synthetic question",
            "question_source_ref": "S001",
            "question_source_kind": "QUESTION",
            "reason": "No reusable answer was established.",
        }]

    def test_prompt_measurements_report_each_requested_utf8_component(self) -> None:
        for count in (8, 87):
            with self.subTest(source_count=count):
                fixture = self.measurement_fixture(count)
                metrics = actionable_render.actionable_prompt_measurements(
                    fixture
                )

                self.assertEqual(
                    set(metrics),
                    {
                        "instruction_bytes",
                        "schema_bytes",
                        "projected_sources_bytes",
                        "total_prompt_bytes",
                    },
                )
                self.assertTrue(
                    all(isinstance(value, int) and value > 0 for value in metrics.values())
                )
                self.assertGreaterEqual(
                    metrics["total_prompt_bytes"],
                    metrics["instruction_bytes"]
                    + metrics["schema_bytes"]
                    + metrics["projected_sources_bytes"],
                )
                refs = list(actionable_source_map(fixture))
                projected = json.dumps(project_model_sources(fixture, refs))
                schema = actionable_schema(fixture)
                prompt = actionable_render.actionable_prompt(fixture)
                self.assertEqual(
                    metrics["instruction_bytes"],
                    len(prompt.encode("utf-8")) - len(projected.encode("utf-8")),
                )
                self.assertEqual(
                    metrics["schema_bytes"],
                    len(json.dumps(schema, separators=(",", ":")).encode("utf-8")),
                )
                self.assertEqual(
                    metrics["projected_sources_bytes"],
                    len(projected.encode("utf-8")),
                )
                self.assertEqual(
                    metrics["total_prompt_bytes"],
                    len((prompt + structured_output_contract(schema)).encode("utf-8")),
                )

    def test_provenance_independently_rejects_invalid_disposition_rows(self) -> None:
        for label, dispositions in (
            ("object", {"S001": "INCLUDE", "S002": "INCLUDE", "S003": "INCLUDE"}),
            ("missing", [{"source_ref": "S001", "value": "INCLUDE"}]),
            ("unknown", [
                {"source_ref": "S001", "value": "INCLUDE"},
                {"source_ref": "S002", "value": "INCLUDE"},
                {"source_ref": "S999", "value": "INCLUDE"},
            ]),
        ):
            with self.subTest(label=label):
                response = copy.deepcopy(self.response())
                response["dispositions"] = dispositions
                with self.assertRaisesRegex(ModelFailure, "source disposition"):
                    actionable_render.actionable_provenance(
                        json.dumps(response), self.sources()
                    )

    def test_unhashable_reference_payloads_fail_closed_as_model_failure(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, object]], object]]] = [
            (
                "disposition value",
                lambda response: response["dispositions"][0].update(value=["INCLUDE"]),
            ),
            (
                "disposition source_ref",
                lambda response: response["dispositions"][0].update(source_ref=["S001"]),
            ),
            (
                "source_refs element",
                lambda response: response["topics"][0].update(source_refs=[{"S001": 1}]),
            ),
            (
                "raw_keep_refs element",
                lambda response: response["topics"][0].update(raw_keep_refs=[["S001"]]),
            ),
            (
                "reference_refs element",
                lambda response: response["topics"][0].update(reference_refs=[["S003"]]),
            ),
            (
                "specific source_ref",
                lambda response: response["topics"][0]["specifics"][0].update(
                    source_ref=["S002"]
                ),
            ),
            (
                "specific value",
                lambda response: response["topics"][0]["specifics"][0].update(
                    value=["/synthetic/service/check"]
                ),
            ),
            (
                "question_source_ref",
                lambda response: response["topics"][0].update(question_source_ref=["S001"]),
            ),
            (
                "unanswered question_source_ref",
                self._replace_with_unhashable_unanswered,
            ),
            (
                "unanswered context_refs element",
                self._replace_with_unhashable_unanswered_context,
            ),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                response = copy.deepcopy(self.response())
                mutate(response)
                with self.assertRaises(ModelFailure):
                    self.render(response)

    @staticmethod
    def _replace_with_unhashable_unanswered(response: dict[str, object]) -> None:
        response["topics"] = []
        response["unanswered"] = [{
            "topic": "Synthetic question",
            "question_source_ref": ["S001"],
            "question_source_kind": "QUESTION",
            "context_refs": [],
            "reason": "No reusable answer was established.",
        }]

    @staticmethod
    def _replace_with_unhashable_unanswered_context(response: dict[str, object]) -> None:
        response["topics"] = []
        response["unanswered"] = [{
            "topic": "Synthetic question",
            "question_source_ref": "S001",
            "question_source_kind": "QUESTION",
            "context_refs": [["S002"]],
            "reason": "No reusable answer was established.",
        }]

    def test_contract_retains_nondeterministic_reader_quality_rules(self) -> None:
        instructions = actionable_render.actionable_prompt(self.sources()).split(
            "\nSources:\n", 1
        )[0]

        for rule in (
            "A title must not introduce a program, product, meeting, migration, decision, "
            "owner, or follow-up absent from that topic's sources",
            "normally 3\u20138 when the source volume supports them",
            "80\u201395% message reduction",
            "one complete sentence of a declared topic source",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, instructions)

    def test_numbered_contract_names_every_model_owned_semantic_decision(self) -> None:
        instructions = actionable_render.actionable_prompt(self.sources()).split(
            "\nSources:\n", 1
        )[0]

        for number in range(1, 11):
            self.assertIn(f"{number}.", instructions)
        for responsibility in (
            "disposition",
            "Retain reusable engineering knowledge",
            "grouping",
            "corrections and supersession",
            "assignments",
            "QUESTION",
            "ISSUE",
            "partial or resolved",
            "unanswered",
            "limitation",
            "actions",
            "uncertainty",
            "documented, confirmed, or field_guidance",
        ):
            with self.subTest(responsibility=responsibility):
                self.assertIn(responsibility, instructions)


if __name__ == "__main__":
    unittest.main()
