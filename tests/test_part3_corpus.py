from __future__ import annotations

import json
import unittest
from pathlib import Path

from urllib.request import HTTPRedirectHandler, ProxyHandler

from whatsapp_tech_digest.models import ModelFailure, StaticModel, _classifier_prompt, _classifier_schema, evaluate_high_recall_corpus, high_recall_select, render_grounded, stage_zero
from whatsapp_tech_digest.ollama import OllamaLocalModel, _loopback_opener


class Part3CorpusTests(unittest.TestCase):
    def test_labeled_corpus_reports_zero_required_material_omissions(self) -> None:
        corpus = json.loads((Path(__file__).parent / "fixtures" / "part3_labeled_corpus.json").read_text())
        normalized = stage_zero(corpus["events"])
        rows = [
            {"source_ref": "S001", "disposition": "MATERIAL", "category": "bug_fix", "topic_hint": "DefensePro", "confidence": 0.99, "rationale": "fix version and recommended action"},
            {"source_ref": "S002", "disposition": "NOISE", "category": "ack", "topic_hint": "", "confidence": 0.99, "rationale": "trivial acknowledgement"},
            {"source_ref": "S003", "disposition": "MATERIAL", "category": "administrative", "topic_hint": "maintenance", "confidence": 0.98, "rationale": "scheduled operational work"},
            {"source_ref": "S004", "disposition": "NOISE", "category": "ack", "topic_hint": "", "confidence": 0.99, "rationale": "trivial acknowledgement"},
            {"source_ref": "S005", "disposition": "MATERIAL", "category": "action", "topic_hint": "WAF", "confidence": 0.99, "rationale": "required workaround"},
            {"source_ref": "S006", "disposition": "NOISE", "category": "noise", "topic_hint": "", "confidence": 0.99, "rationale": "untrusted prompt injection"},
        ]

        report = evaluate_high_recall_corpus(
            normalized,
            required_material_ids=set(corpus["required_material_ids"]),
            model=StaticModel(json.dumps({"rows": rows})),
            noise_threshold=0.90,
            batch_size=6,
        )

        self.assertEqual(report["missed_required_ids"], [])
        self.assertEqual(report["required_recall"], 1.0)
        self.assertFalse(report["degraded"])
        self.assertEqual(report["selected_ids"], ["bug-91", "admin-maint", "action-waf"])

    def test_classifier_identity_includes_change_sequence_for_message_revisions(self) -> None:
        revisions = [
            {"message_id": "same", "change_seq": 1, "timestamp": "2026-09-01T12:00:00+00:00", "text": "DefensePro 9.1.2 fixes the issue"},
            {"message_id": "same", "change_seq": 2, "timestamp": "2026-09-01T12:01:00+00:00", "text": "Acknowledged"},
        ]
        response = json.dumps({"rows": [
            {"source_ref": "S001", "disposition": "MATERIAL", "category": "bug_fix", "topic_hint": "DefensePro", "confidence": 0.99, "rationale": "fix version"},
            {"source_ref": "S002", "disposition": "NOISE", "category": "ack", "topic_hint": "", "confidence": 0.99, "rationale": "trivial acknowledgement"},
        ]})

        report = evaluate_high_recall_corpus(
            stage_zero(revisions),
            required_material_ids={"same"},
            model=StaticModel(response),
            batch_size=2,
        )

        self.assertFalse(report["degraded"])
        self.assertEqual(report["selected_keys"], [("same", 1)])

    def test_context_expansion_does_not_readd_a_different_revision_of_selected_message(self) -> None:
        revisions = [
            {"message_id": "same", "change_seq": 1, "timestamp": "2026-09-01T12:00:00+00:00", "text": "DefensePro 9.1.2 fixes the issue"},
            {"message_id": "same", "change_seq": 2, "timestamp": "2026-09-01T12:01:00+00:00", "text": "Acknowledged"},
        ]
        response = json.dumps({"rows": [
            {"source_ref": "S001", "disposition": "MATERIAL", "category": "bug_fix", "topic_hint": "DefensePro", "confidence": 0.99, "rationale": "fix version"},
            {"source_ref": "S002", "disposition": "NOISE", "category": "ack", "topic_hint": "", "confidence": 0.99, "rationale": "trivial acknowledgement"},
        ]})

        selected, degraded = high_recall_select(stage_zero(revisions), StaticModel(response), batch_size=2)

        self.assertFalse(degraded)
        self.assertEqual([(item["message_id"], item["change_seq"]) for item in selected], [("same", 1)])

    def test_grounded_final_output_requires_immutable_revision_references(self) -> None:
        revisions = [
            {"message_id": "same", "change_seq": 1, "text": "DefensePro 9.1.2 fixes the issue"},
            {"message_id": "same", "change_seq": 2, "text": "Acknowledged"},
        ]
        response = json.dumps({
            "dispositions": {'["same",1]': "INCLUDE", '["same",2]': "EXCLUDE"},
            "claims": [{"source_refs": ['["same",1]'], "claim": "DefensePro 9.1.2 fixes the issue"}],
        })

        self.assertEqual(render_grounded(response, revisions), "DefensePro 9.1.2 fixes the issue [same#1]")

    def test_grounded_final_output_rejects_claim_from_excluded_source(self) -> None:
        items = [
            {"message_id": "included", "change_seq": 1, "text": "Apply the workaround"},
            {"message_id": "excluded", "change_seq": 2, "text": "Internal-only detail"},
        ]
        response = json.dumps({
            "dispositions": {'["included",1]': "INCLUDE", '["excluded",2]': "EXCLUDE"},
            "claims": [{"source_refs": ['["excluded",2]'], "claim": "Internal-only detail"}],
        })

        with self.assertRaises(ModelFailure):
            render_grounded(response, items)

    def test_loopback_transport_has_no_proxy_or_redirect_handler(self) -> None:
        opener = _loopback_opener()
        proxy_handlers = [handler for handler in opener.handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(proxy_handlers, [])
        self.assertFalse(any(isinstance(handler, HTTPRedirectHandler) for handler in opener.handlers))
        self.assertEqual([type(handler).__name__ for handler in opener.handle_open["http"]], ["HTTPHandler"])

    def test_local_model_adapter_rejects_non_loopback_endpoint(self) -> None:
        with self.assertRaises(ValueError):
            OllamaLocalModel("qwen3.5:4b", endpoint="http://192.168.18.200:11434/api/generate")

    def test_local_model_adapter_normalizes_loopback_root_to_generate_endpoint(self) -> None:
        model = OllamaLocalModel("qwen3.5:4b", endpoint="http://127.0.0.1:11434")
        self.assertEqual(model.endpoint, "http://127.0.0.1:11434/api/generate")

    def test_classifier_schema_bounds_nonessential_explanation_fields(self) -> None:
        schema = _classifier_schema([{"message_id": "a", "change_seq": 1, "text": "technical update"}])
        row = schema["properties"]["rows"]["items"]
        self.assertEqual(row["properties"]["category"]["maxLength"], 48)
        self.assertEqual(row["properties"]["topic_hint"]["maxLength"], 80)
        self.assertEqual(row["properties"]["rationale"]["maxLength"], 160)

    def test_local_model_adapter_requests_bounded_nonthinking_schema_output(self) -> None:
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"response":"{}"}'

        def opener(request, timeout):
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return Response()

        schema = {"type": "object", "required": ["rows"]}
        model = OllamaLocalModel("qwen3.5:4b", timeout=17, max_output_tokens=42, opener=opener)
        self.assertEqual(model.complete_structured("classify", schema), "{}")
        self.assertEqual(captured["timeout"], 17)
        self.assertEqual(captured["payload"]["think"], False)
        self.assertEqual(captured["payload"]["format"], schema)
        self.assertEqual(captured["payload"]["options"]["num_predict"], 42)

    def test_classifier_prompt_keeps_technical_requests_for_unanswered_question_review(self) -> None:
        prompt = _classifier_prompt(stage_zero([{
            "message_id": "request", "change_seq": 1,
            "text": "Does anybody know whether a diagnostic utility exists for exported records?",
        }]))
        self.assertIn("technical question or request", prompt)
        self.assertIn("MATERIAL or UNCERTAIN", prompt)

    def test_classifier_projection_uses_opaque_refs_and_restores_local_rows(self) -> None:
        raw_id = "raw-whatsapp-message-id"
        items = stage_zero([{
            "message_id": raw_id, "change_seq": 1,
            "text": "token=STAGE3B_SYNTHETIC_SECRET technical update",
            "participant": "15551230000@s.whatsapp.net",
        }])
        prompt = _classifier_prompt(items)
        schema = _classifier_schema(items)

        self.assertNotIn(raw_id, prompt)
        self.assertNotIn("STAGE3B_SYNTHETIC_SECRET", prompt)
        self.assertIn("S001", prompt)
        self.assertIn("[REDACTED]", prompt)
        row_properties = schema["properties"]["rows"]["items"]["properties"]
        self.assertIn("source_ref", row_properties)
        self.assertNotIn("message_id", row_properties)
        selected, degraded = high_recall_select(
            items,
            StaticModel(json.dumps({"rows": [{
                "source_ref": "S001", "disposition": "MATERIAL", "category": "update",
                "topic_hint": "technical", "confidence": 0.99, "rationale": "technical update",
            }]})),
        )
        self.assertFalse(degraded)
        self.assertEqual([(item["message_id"], item["change_seq"]) for item in selected], [(raw_id, 1)])

    def test_classifier_prompt_passes_explicit_policy_to_model(self) -> None:
        captured = []

        class CaptureModel:
            def complete(self, prompt):
                captured.append(prompt)
                return json.dumps({"rows": [{
                    "source_ref": "S001", "disposition": "MATERIAL", "category": "upgrade",
                    "topic_hint": "release", "confidence": 0.99, "rationale": "technical release guidance",
                }]})

        high_recall_select(
            stage_zero([{"message_id": "a", "change_seq": 1, "text": "Use the direct supported release upgrade path."}]),
            CaptureModel(),
            classifier_instruction="Classify technical operations; acknowledgements are NOISE.",
        )
        self.assertTrue(captured[0].startswith("Classify technical operations; acknowledgements are NOISE."))


if __name__ == "__main__":
    unittest.main()
