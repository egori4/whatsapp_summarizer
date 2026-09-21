from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whatsapp_tech_digest import run
from whatsapp_tech_digest.config import DigestConfig
from whatsapp_tech_digest.ephemeral_two_call import (
    extraction_prompt,
    extraction_prompt_measurements,
    extract_evidence,
    extraction_schema,
    reconciliation_prompt,
    reconciliation_prompt_measurements,
    reconciliation_schema,
    reconcile_evidence,
    summarize_ephemeral_two_call,
)
from whatsapp_tech_digest.model_provider import structured_output_contract
from whatsapp_tech_digest.models import DigestResult, ModelDiagnostic, ModelFailure, StaticModel
from whatsapp_tech_digest.quality_eval import evaluate_window


EXAMPLE_POLICY = Path(__file__).parents[1] / "config" / "digest.policy.example.json"


def _sources():
    return [
        {
            "message_id": "question", "change_seq": 1,
            "timestamp": "2026-09-01T12:00:00+00:00", "display_name": "Engineer A",
            "text": "Which command verifies the synthetic service?", "tracked_item": True,
        },
        {
            "message_id": "answer", "change_seq": 2,
            "timestamp": "2026-09-01T12:01:00+00:00", "display_name": "Engineer B",
            "text": "Run /synthetic/service/check to verify the synthetic service.",
            "reply_to": "question",
        },
        {
            "message_id": "thanks", "change_seq": 3,
            "timestamp": "2026-09-01T12:02:00+00:00", "display_name": "Engineer C",
            "text": "Thanks for the update.",
        },
    ]


def _extraction(answer_text="Run /synthetic/service/check to verify the synthetic service."):
    return json.dumps({"sources": [
        {
            "source_ref": "S001", "disposition": "MATERIAL", "kind": "QUESTION",
            "evidence": [{"role": "QUESTION", "text": "Which command verifies the synthetic service?"}],
        },
        {
            "source_ref": "S002", "disposition": "MATERIAL", "kind": "ANSWER",
            "evidence": [{"role": "ANSWER", "text": answer_text}],
        },
        {"source_ref": "S003", "disposition": "NOISE", "kind": "OTHER", "evidence": []},
    ]})


def _plan(**overrides):
    topic = {
        "title": "Synthetic service verification",
        "atom_refs": ["A001", "A002"],
        "question_atom_ref": "A001",
        "resolution_status": "resolved",
        "situation_atom_refs": ["A002"],
        "action_atom_refs": [],
        "limitation_atom_refs": [],
        "reference_atom_refs": [],
        "confidence": "documented",
    }
    topic.update(overrides)
    return json.dumps({
        "accounting": [
            {"atom_ref": "A001", "value": "INCLUDE"},
            {"atom_ref": "A002", "value": "INCLUDE"},
        ],
        "topics": [topic],
        "unanswered": [],
    })


class _QueueModel:
    def __init__(self, *responses, diagnostic=None):
        self.responses = list(responses)
        self.diagnostic = diagnostic
        self.calls = 0
        self.prompts = []

    def complete_structured(self, prompt, schema):
        self.calls += 1
        self.prompts.append(prompt)
        if self.diagnostic is not None:
            raise ModelFailure("transport failed", diagnostic=self.diagnostic)
        return self.responses.pop(0)


class PhaseCTwoCallTests(unittest.TestCase):
    def test_normal_path_is_two_calls_and_renders_only_validated_atoms(self):
        model = _QueueModel(_extraction(), _plan())

        result = summarize_ephemeral_two_call(_sources(), model, model)

        self.assertEqual(model.calls, 2)
        self.assertIn("Which command verifies the synthetic service?", result.text)
        self.assertIn("Run /synthetic/service/check to verify the synthetic service.", result.text)
        self.assertNotIn("Thanks for the update.", result.text)
        self.assertEqual(result.included_message_ids, ["question", "answer", "thanks"])
        self.assertEqual(result.provenance["schema_version"], "actionable-provenance-v2")

    def test_invalid_extraction_atom_never_reaches_reconciliation(self):
        with self.assertRaises(ModelFailure):
            extract_evidence(_extraction("Invented unsupported answer."), _sources())

        extractor = _QueueModel(
            _extraction("Invented unsupported answer."),
            _extraction("Invented unsupported answer."),
        )
        reconciler = _QueueModel(_plan())
        with self.assertRaises(ModelFailure):
            summarize_ephemeral_two_call(_sources(), extractor, reconciler)
        self.assertEqual(extractor.calls, 2)
        self.assertEqual(reconciler.calls, 0)

    def test_reconciliation_cannot_supply_factual_prose(self):
        atoms = extract_evidence(_extraction(), _sources())
        data = json.loads(_plan())
        data["topics"][0]["situation"] = "Invented prose."

        with self.assertRaises(ModelFailure):
            reconcile_evidence(json.dumps(data), _sources(), atoms)

    def test_one_source_cannot_own_atoms_in_multiple_topics(self):
        items = [{
            "message_id": "status", "change_seq": 1,
            "timestamp": "2026-09-01T12:00:00+00:00",
            "text": "Synthetic alpha is ready. Synthetic beta is ready.",
        }]
        extraction = json.dumps({"sources": [{
            "source_ref": "S001", "disposition": "MATERIAL", "kind": "STATUS",
            "evidence": [
                {"role": "STATUS", "text": "Synthetic alpha is ready."},
                {"role": "STATUS", "text": "Synthetic beta is ready."},
            ],
        }]})
        atoms = extract_evidence(extraction, items)
        plan = json.dumps({
            "accounting": [
                {"atom_ref": "A001", "value": "INCLUDE"},
                {"atom_ref": "A002", "value": "INCLUDE"},
            ],
            "topics": [
                {
                    "title": "Synthetic alpha", "atom_refs": ["A001"],
                    "question_atom_ref": None, "resolution_status": None,
                    "situation_atom_refs": ["A001"], "action_atom_refs": [],
                    "limitation_atom_refs": [], "reference_atom_refs": [],
                    "confidence": "documented",
                },
                {
                    "title": "Synthetic beta", "atom_refs": ["A002"],
                    "question_atom_ref": None, "resolution_status": None,
                    "situation_atom_refs": ["A002"], "action_atom_refs": [],
                    "limitation_atom_refs": [], "reference_atom_refs": [],
                    "confidence": "documented",
                },
            ],
            "unanswered": [],
        })

        with self.assertRaises(ModelFailure):
            reconcile_evidence(plan, items, atoms)

    def test_one_source_cannot_be_both_resolved_and_unanswered(self):
        items = [
            {
                "message_id": "questions", "change_seq": 1,
                "timestamp": "2026-09-01T12:00:00+00:00", "tracked_item": True,
                "text": "Which synthetic check is supported? Which synthetic check is pending?",
            },
            {
                "message_id": "answer", "change_seq": 2,
                "timestamp": "2026-09-01T12:01:00+00:00",
                "text": "The alpha synthetic check is supported.",
            },
        ]
        extraction = json.dumps({"sources": [
            {
                "source_ref": "S001", "disposition": "MATERIAL", "kind": "QUESTION",
                "evidence": [
                    {"role": "QUESTION", "text": "Which synthetic check is supported?"},
                    {"role": "QUESTION", "text": "Which synthetic check is pending?"},
                ],
            },
            {
                "source_ref": "S002", "disposition": "MATERIAL", "kind": "ANSWER",
                "evidence": [{"role": "ANSWER", "text": "The alpha synthetic check is supported."}],
            },
        ]})
        atoms = extract_evidence(extraction, items)
        plan = json.dumps({
            "accounting": [
                {"atom_ref": "A001", "value": "INCLUDE"},
                {"atom_ref": "A002", "value": "INCLUDE"},
                {"atom_ref": "A003", "value": "INCLUDE"},
            ],
            "topics": [{
                "title": "Supported synthetic check", "atom_refs": ["A001", "A003"],
                "question_atom_ref": "A001", "resolution_status": "resolved",
                "situation_atom_refs": ["A003"], "action_atom_refs": [],
                "limitation_atom_refs": [], "reference_atom_refs": [],
                "confidence": "documented",
            }],
            "unanswered": [{"atom_ref": "A002", "context_atom_refs": []}],
        })

        with self.assertRaises(ModelFailure):
            reconcile_evidence(plan, items, atoms)

    def test_untracked_edited_update_cannot_self_resolve(self):
        items = [{
            "message_id": "edit", "change_seq": 1,
            "timestamp": "2026-09-01T12:00:00+00:00", "change_type": "edit",
            "text": "The synthetic edit is complete.",
        }]
        extraction = json.dumps({"sources": [{
            "source_ref": "S001", "disposition": "MATERIAL", "kind": "UPDATE",
            "evidence": [{"role": "UPDATE", "text": "The synthetic edit is complete."}],
        }]})
        atoms = extract_evidence(extraction, items)
        plan = json.dumps({
            "accounting": [{"atom_ref": "A001", "value": "INCLUDE"}],
            "topics": [{
                "title": "Synthetic edit", "atom_refs": ["A001"],
                "question_atom_ref": "A001", "resolution_status": "resolved",
                "situation_atom_refs": [], "action_atom_refs": [],
                "limitation_atom_refs": [], "reference_atom_refs": [],
                "confidence": "documented",
            }],
            "unanswered": [],
        })

        with self.assertRaises(ModelFailure):
            reconcile_evidence(plan, items, atoms)

    def test_extraction_validation_repair_is_closed_code_and_three_calls_maximum(self):
        model = _QueueModel(
            _extraction("Invented unsupported answer."),
            _extraction(),
            _plan(),
        )

        result = summarize_ephemeral_two_call(_sources(), model, model)

        self.assertEqual(model.calls, 3)
        self.assertTrue(result.degraded)
        self.assertIn("Failure code: GROUNDING", model.prompts[1])
        self.assertNotIn("Invented unsupported answer", model.prompts[1])
        self.assertIn("Synthetic service verification", result.text)

    def test_transport_failure_is_terminal_without_repair(self):
        model = _QueueModel(diagnostic=ModelDiagnostic(category="timeout", stage="extract"))

        with self.assertRaises(ModelFailure):
            summarize_ephemeral_two_call(_sources(), model, model)

        self.assertEqual(model.calls, 1)

    def test_empty_output_is_terminal_without_repair(self):
        model = _QueueModel("")

        with self.assertRaises(ModelFailure):
            summarize_ephemeral_two_call(_sources(), model, model)

        self.assertEqual(model.calls, 1)

    def test_reconciliation_receives_atoms_not_noise_or_message_ids(self):
        model = _QueueModel(_extraction(), _plan())

        summarize_ephemeral_two_call(_sources(), model, model)

        reconciliation_prompt = model.prompts[1]
        self.assertIn("A001", reconciliation_prompt)
        self.assertIn("A002", reconciliation_prompt)
        self.assertIn('"disposition": "MATERIAL"', reconciliation_prompt)
        self.assertIn('"reply_to_source_refs": ["S001"]', reconciliation_prompt)
        self.assertNotIn("Thanks for the update", reconciliation_prompt)
        self.assertNotIn('"message_id"', reconciliation_prompt)
        self.assertNotIn('"display_name"', reconciliation_prompt)

    def test_reconciliation_prompt_states_bidirectional_accounting_contract(self):
        atoms = extract_evidence(_extraction(), _sources())

        prompt = reconciliation_prompt(atoms)

        self.assertIn(
            "Every reader-facing atom must be INCLUDE or UNCERTAIN.",
            prompt,
        )
        self.assertIn(
            "CONTEXT atoms may appear only as non-rendered topic atoms or unanswered context",
            prompt,
        )
        self.assertIn(
            "EXCLUDE atoms must not appear in any topic or unanswered entry.",
            prompt,
        )
        self.assertIn(
            "All atoms from one source must belong to at most one topic or unanswered entry.",
            prompt,
        )

    def test_policy_instruction_never_crosses_into_reconciliation_or_its_repair(self):
        policy_sentinel = "POLICY_SENTINEL_DO_NOT_FORWARD"
        invalid = json.loads(_plan())
        invalid["topics"][0]["factual_prose"] = "not allowed"
        extractor = _QueueModel(_extraction())
        reconciler = _QueueModel(json.dumps(invalid), _plan())

        result = summarize_ephemeral_two_call(
            _sources(), extractor, reconciler, final_instruction=policy_sentinel,
        )

        self.assertIn(policy_sentinel, extractor.prompts[0])
        self.assertEqual(reconciler.calls, 2)
        self.assertTrue(all(policy_sentinel not in prompt for prompt in reconciler.prompts))
        self.assertIn("Failure code: SCHEMA", reconciler.prompts[1])
        self.assertIn("Synthetic service verification", result.text)

    def test_prompt_measurements_cover_exact_structured_requests(self):
        items = _sources()
        atoms = extract_evidence(_extraction(), items)

        extraction = extraction_prompt_measurements(items)
        reconciliation = reconciliation_prompt_measurements(atoms)

        self.assertEqual(
            extraction["total_prompt_bytes"],
            len((extraction_prompt(items) + structured_output_contract(extraction_schema(items))).encode()),
        )
        self.assertEqual(
            reconciliation["total_prompt_bytes"],
            len((reconciliation_prompt(atoms) + structured_output_contract(reconciliation_schema(atoms))).encode()),
        )

    def test_two_call_mode_is_a_hermes_production_candidate(self):
        policy = json.loads(EXAMPLE_POLICY.read_text(encoding="utf-8"))
        policy["models"].update({
            "pipeline_mode": "two_call",
            "provider": "hermes-openai-codex",
            "endpoint": "local://hermes-cli",
        })

        DigestConfig.from_dict(policy).require_production_pipeline()

    def test_synthetic_evaluator_routes_two_call_and_counts_two_calls(self):
        window = {
            "id": "two-call", "material": True, "events": _sources(),
            "expected": {
                "required_atoms": [
                    "Which command verifies the synthetic service?",
                    "Run /synthetic/service/check to verify the synthetic service.",
                ],
                "optional_atoms": [], "answered_question_ids": ["question"],
                "unanswered_ids": [], "limited_update_question_ids": [],
                "forbidden_unsupported_atoms": [], "forbidden_superseded_atoms": [],
                "privacy_sentinels": [],
            },
        }

        outcome = evaluate_window(
            window, 1, StaticModel(_extraction()), StaticModel(_plan()),
            pipeline_mode="two_call",
        )

        self.assertTrue(outcome["accepted"])
        self.assertEqual(outcome["model_calls"], 2)
        self.assertEqual(outcome["score"]["classification_error_count"], 0)

    def test_runner_selects_two_call_without_preclassification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = json.loads(EXAMPLE_POLICY.read_text(encoding="utf-8"))
            policy["models"].update({
                "pipeline_mode": "two_call", "provider": "hermes-openai-codex",
                "endpoint": "local://hermes-cli", "final": "gpt-5.6-terra",
                "fallback": "gpt-5.6-terra",
            })
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            spool_path = root / "spool.sqlite3"
            spool = run.DurableSpool(spool_path, policy["target_group_jid"])
            spool.append_message({
                "chat_jid": policy["target_group_jid"], "message_id": "update",
                "participant": "15551234567@s.whatsapp.net", "change_seq": 1,
                "timestamp": "2026-09-01T12:00:00+00:00",
                "text": "The synthetic service check is documented.",
            })
            result = DigestResult(
                "# Technical Updates — Sep 1\n\n## Synthetic service\nSummary: The synthetic service check is documented.",
                ["update"], "two-call", False,
                {"schema_version": "actionable-provenance-v2", "topics": [{
                    "source_revisions": [["update", 1]],
                    "raw_keep_revisions": [["update", 1]], "reference_revisions": [],
                    "question_revisions": [], "question_resolution": None,
                    "contributor_revisions": [["update", 1]],
                }], "unanswered": []},
            )

            with patch("whatsapp_tech_digest.run.build_model", return_value=object()), patch(
                "whatsapp_tech_digest.run.high_recall_select",
                side_effect=AssertionError("two_call must bypass the legacy preclassifier"),
            ), patch(
                "whatsapp_tech_digest.run.summarize_ephemeral_two_call", return_value=result,
            ) as summarize:
                output = run.generate(policy_path, spool_path, execution_mode="render-only")

            self.assertEqual(output, result.text)
            summarize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
