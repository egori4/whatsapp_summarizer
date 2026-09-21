from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from whatsapp_tech_digest.quality_eval import (
    aggregate_results,
    evaluate_corpus,
    evaluation_items,
    evaluate_window,
    load_corpus,
    load_evaluation_models,
    score_candidate,
)
from whatsapp_tech_digest.models import FailingModel, StaticModel


ROOT = Path(__file__).parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "one_pass_quality_corpus.json"
POLICY = ROOT / "config" / "one_pass_evaluation.policy.example.json"


class OnePassQualityEvaluationTests(unittest.TestCase):
    def test_current_behavior_version_is_consistent(self) -> None:
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        package = (ROOT / "whatsapp_tech_digest" / "__init__.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(project, r'(?m)^version = "1\.1\.0"$')
        self.assertRegex(package, r'(?m)^__version__ = "1\.1\.0"$')
        self.assertIsNotNone(re.search(r"\| `1\.1\.0` \|.*\(current\)", readme))

    def test_corpus_has_eight_synthetic_windows_and_declared_coverage(self) -> None:
        corpus = load_corpus(CORPUS)

        self.assertEqual(len(corpus["windows"]), 8)
        self.assertEqual(len({window["id"] for window in corpus["windows"]}), 8)
        self.assertEqual(
            set(corpus["coverage"]),
            {
                "important_announcements_and_assignments",
                "answered_technical_questions",
                "unanswered_and_limited_guidance",
                "corrections_and_superseded_advice",
                "limitations_and_uncertain_field_guidance",
                "privacy_sensitive_identifier_forms",
                "fabricated_migration_topic_regression",
                "all_nonmaterial",
            },
        )
        self.assertEqual(sum(not window["material"] for window in corpus["windows"]), 1)
        self.assertTrue(all(window["expected"]["required_atoms"] for window in corpus["windows"] if window["material"]))
        self.assertTrue(any(window["expected"]["optional_atoms"] for window in corpus["windows"] if window["material"]))

    def test_corpus_rejects_identity_metadata_and_non_invalid_urls(self) -> None:
        data = json.loads(CORPUS.read_text(encoding="utf-8"))
        data["windows"][0]["events"][0]["participant"] = "15551234567@s.whatsapp.net"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "forbidden event field"):
                load_corpus(path)

    def test_privacy_window_redacts_api_token_assignment_before_projection(self) -> None:
        window = next(window for window in load_corpus(CORPUS)["windows"] if window["id"] == "identifier_privacy")
        item = next(item for item in evaluation_items(window) if item["message_id"] == "private-secret")
        self.assertEqual(item["redacted_text"], "Use [REDACTED] for the temporary test.")

        data = json.loads(CORPUS.read_text(encoding="utf-8"))
        data["windows"][0]["events"][0]["text"] = "See https://example.com/private"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "public fixture URL"):
                load_corpus(path)

    def test_candidate_scoring_treats_limited_guidance_as_update_not_category(self) -> None:
        window = next(window for window in load_corpus(CORPUS)["windows"] if window["id"] == "open_and_limited")
        response = {
            "dispositions": {"S001": "INCLUDE", "S002": "INCLUDE", "S003": "INCLUDE", "S004": "INCLUDE"},
            "topics": [{
                "topic": "Synchronization recovery", "title": "Synchronization recovery",
                "source_refs": ["S001", "S002", "S003"], "raw_keep_refs": ["S001", "S002", "S003"],
                "question": "How can we restore API synchronization after the gateway upgrade?",
                "question_source_ref": "S001", "question_source_kind": "QUESTION",
                "situation": "Restarting the sync worker restores new jobs, but historical jobs remain missing.",
                "recommendation": "Restarting the sync worker restores new jobs, but historical jobs remain missing.",
                "actions": [], "specifics": [],
                "limitation": "This workaround is field guidance only because the root cause is not documented.",
                "reference_refs": [], "resolution_status": "partial", "confidence": "field_guidance",
            }],
            "unanswered": [{
                "topic": "Archive export", "question_source_ref": "S004",
                "question_source_kind": "QUESTION", "context_refs": [],
                "reason": "No reusable answer was established.",
            }],
        }
        rendered = (
            "How can we restore API synchronization after the gateway upgrade?\n"
            "Restarting the sync worker restores new jobs, but historical jobs remain missing.\n"
            "This workaround is field guidance only because the root cause is not documented.\n"
            "Which supported utility exports archived audit records?"
        )

        score = score_candidate(window, response, rendered, "sanitized prompt")

        self.assertEqual(score["classification_error_count"], 0)
        self.assertEqual(score["missing_required_atoms"], [])
        self.assertEqual(score["privacy_leak_count"], 0)

    def test_candidate_scoring_counts_omission_privacy_supersession_and_known_invention(self) -> None:
        corpus = load_corpus(CORPUS)
        correction = next(window for window in corpus["windows"] if window["id"] == "correction_chain")
        score = score_candidate(
            correction,
            {"dispositions": {}, "topics": [], "unanswered": []},
            "For Router Q4, use release 8.2 before upgrading to 9.0.",
            "prompt",
        )
        self.assertGreater(score["missing_required_count"], 0)
        self.assertEqual(score["supersession_error_count"], 1)

        regression = next(window for window in corpus["windows"] if window["id"] == "fabricated_migration_topic")
        score = score_candidate(
            regression,
            {"dispositions": {}, "topics": [], "unanswered": []},
            "A migration workshop was scheduled for next week.",
            "prompt",
        )
        self.assertEqual(score["known_unsupported_count"], 2)

        privacy = next(window for window in corpus["windows"] if window["id"] == "identifier_privacy")
        sentinel = privacy["expected"]["privacy_sentinels"][0]
        score = score_candidate(
            privacy,
            {"dispositions": {}, "topics": [], "unanswered": []},
            f"Contact {sentinel}",
            "sanitized prompt",
        )
        self.assertEqual(score["privacy_leak_count"], 1)

    def test_candidate_scoring_counts_an_unexpected_resolution_entry(self) -> None:
        window = next(window for window in load_corpus(CORPUS)["windows"] if window["id"] == "administrative_assignment")
        score = score_candidate(
            window,
            {"dispositions": {}, "topics": [], "unanswered": [{"question_source_ref": "S001"}]},
            "\n".join(window["expected"]["required_atoms"]),
            "prompt",
        )
        self.assertEqual(score["classification_error_count"], 1)

    def test_aggregate_separates_smoke_test_gates_and_worst_case_call_budget(self) -> None:
        material = []
        for index in range(14):
            material.append({
                "window_id": f"m{index}", "run_index": index % 2 + 1, "material": True,
                "accepted": True, "model_calls": 2 if index < 2 else 1,
                "score": {
                    "required_total": 1, "missing_required_count": 0,
                    "optional_total": 1, "missing_optional_count": 1,
                    "classification_error_count": 0, "privacy_leak_count": 0,
                    "known_unsupported_count": 0, "supersession_error_count": 0,
                },
            })
        nonmaterial = [{
            "window_id": "n", "run_index": index, "material": False,
            "accepted": False, "model_calls": 2, "score": None,
        } for index in (1, 2)]
        reviews = {
            f"m{index}:{index % 2 + 1}": {"unsupported_accepted_count": 0}
            for index in range(14)
        }

        report = aggregate_results(material + nonmaterial, reviews, expected_runs=16)

        self.assertEqual(report["evaluation_runs"], 16)
        self.assertEqual(report["model_invocations"], 20)
        self.assertEqual(report["maximum_model_invocations"], 32)
        self.assertEqual(report["material_retry_rate"], 2 / 14)
        self.assertEqual(report["material_failure_rate"], 0.0)
        self.assertEqual(report["nonmaterial_fail_closed_count"], 2)
        self.assertEqual(report["optional_atom_recall"], 0.0)
        self.assertEqual(report["model_quality_decision"], "accepted")
        self.assertEqual(report["reliability_interpretation"], "exploratory_repeatability_only")

    def test_model_quality_decision_waits_for_manual_unsupported_content_review(self) -> None:
        result = {
            "window_id": "m", "run_index": 1, "material": True, "accepted": True,
            "model_calls": 1,
            "score": {
                "required_total": 1, "missing_required_count": 0,
                "optional_total": 0, "missing_optional_count": 0,
                "classification_error_count": 0, "privacy_leak_count": 0,
                "known_unsupported_count": 0, "supersession_error_count": 0,
            },
        }
        report = aggregate_results([result], {}, expected_runs=1)
        self.assertEqual(report["model_quality_decision"], "pending_manual_review")

    def test_prompt_privacy_leak_is_counted_even_when_the_run_is_rejected(self) -> None:
        result = {
            "window_id": "identifier_privacy", "run_index": 1, "material": True,
            "accepted": False, "model_calls": 2,
            "score": {
                "required_total": 1, "missing_required_count": 0,
                "optional_total": 0, "missing_optional_count": 0,
                "classification_error_count": 0, "privacy_leak_count": 1,
                "known_unsupported_count": 0, "supersession_error_count": 0,
            },
        }

        report = aggregate_results([result], {}, expected_runs=1)

        self.assertEqual(report["privacy_leak_count"], 1)
        self.assertEqual(report["model_quality_decision"], "not_accepted")

    def test_evaluation_models_use_validated_example_policy_and_provider_builder(self) -> None:
        config, final_model, fallback_model = load_evaluation_models(POLICY)

        self.assertTrue(config.example_only)
        self.assertEqual(config.models.pipeline_mode, "one_pass")
        self.assertEqual(config.models.provider, "hermes-openai-codex")
        self.assertEqual(config.models.final, "gpt-5.6-terra")
        self.assertEqual(config.models.fallback, "gpt-5.6-terra")
        self.assertEqual(config.models.reasoning_effort, "high")
        self.assertEqual(final_model.model, config.models.final)
        self.assertEqual(fallback_model.model, config.models.fallback)

    def test_evaluation_model_loader_rejects_a_different_model_or_reasoning_effort(self) -> None:
        for field, value in (("final", "different-model"), ("reasoning_effort", "medium")):
            data = json.loads(POLICY.read_text(encoding="utf-8"))
            data["models"][field] = value
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "gpt-5.6-terra with high reasoning"):
                    load_evaluation_models(path)

    def test_evaluation_model_loader_rejects_a_nonexample_policy(self) -> None:
        data = json.loads(POLICY.read_text(encoding="utf-8"))
        data["example_only"] = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "example-only policy"):
                load_evaluation_models(path)

    def test_static_window_execution_counts_calls_and_writes_owner_only_evidence(self) -> None:
        window = next(window for window in load_corpus(CORPUS)["windows"] if window["id"] == "answered_question")
        response = json.dumps({
            "dispositions": [
                {"source_ref": "S001", "value": "INCLUDE"},
                {"source_ref": "S002", "value": "INCLUDE"},
                {"source_ref": "S003", "value": "INCLUDE"},
            ],
            "topics": [{
                "topic": "Interface state", "title": "Interface state",
                "source_refs": ["S001", "S002", "S003"],
                "raw_keep_refs": ["S001", "S002", "S003"],
                "question": "Which CLI command shows the active interface state on NetBox X7?",
                "question_source_ref": "S001", "question_source_kind": "QUESTION",
                "situation": "", "recommendation": "Use /show/interface/state on NetBox X7.",
                "actions": [],
                "specifics": [{"source_ref": "S002", "value": "/show/interface/state"}],
                "limitation": "", "reference_refs": ["S003"], "resolution_status": "resolved", "confidence": "documented",
            }],
            "unanswered": [],
        })
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "run.json"
            outcome = evaluate_window(
                window, 1, StaticModel(response), FailingModel(),
                final_instruction="Fixture instruction.", artifact_path=artifact,
            )

            self.assertTrue(outcome["accepted"])
            self.assertEqual(outcome["model_calls"], 1)
            self.assertEqual(outcome["score"]["missing_required_count"], 0)
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            evidence = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertIn("prompts", evidence)
            self.assertIn("raw_responses", evidence)
            self.assertIn("rendered_digest", evidence)
            self.assertNotIn("prompts", outcome)
            self.assertNotIn("raw_responses", outcome)
            self.assertNotIn("rendered_digest", outcome)

    def test_corpus_execution_is_sixteen_runs_and_at_most_thirty_two_calls(self) -> None:
        corpus = load_corpus(CORPUS)
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory) / "evidence"
            results = evaluate_corpus(
                corpus, FailingModel(), FailingModel(), artifact_dir,
                repeats=2, final_instruction="Fixture instruction.",
            )

            self.assertEqual(len(results), 16)
            # A transport failure is terminal, so each run costs exactly one call.
            self.assertEqual(sum(result["model_calls"] for result in results), 16)
            self.assertLessEqual(sum(result["model_calls"] for result in results), 32)
            self.assertTrue(all(not result["accepted"] for result in results))
            self.assertGreater(sum(result["score"]["missing_required_count"] for result in results), 0)
            self.assertEqual(len(list(artifact_dir.glob("run-*.json"))), 16)
            self.assertTrue((artifact_dir / "results.json").is_file())
            self.assertEqual(artifact_dir.stat().st_mode & 0o777, 0o700)

    def test_focused_rerun_selects_two_windows_for_four_runs(self) -> None:
        corpus = load_corpus(CORPUS)
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory) / "focused-evidence"
            results = evaluate_corpus(
                corpus, FailingModel(), FailingModel(), artifact_dir,
                repeats=2,
                window_ids={"identifier_privacy", "fabricated_migration_topic"},
            )

            self.assertEqual(len(results), 4)
            self.assertEqual(
                {result["window_id"] for result in results},
                {"identifier_privacy", "fabricated_migration_topic"},
            )
            index = json.loads((artifact_dir / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(index["expected_runs"], 4)

    def test_detailed_evidence_is_rejected_inside_the_repository(self) -> None:
        corpus = load_corpus(CORPUS)
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            evaluate_corpus(
                corpus, FailingModel(), FailingModel(), ROOT / ".forbidden-evidence",
                repeats=2,
            )
        self.assertFalse((ROOT / ".forbidden-evidence").exists())


if __name__ == "__main__":
    unittest.main()
