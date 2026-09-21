from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .config import DigestConfig
from .ephemeral_two_call import extract_evidence, summarize_ephemeral_two_call
from .model_provider import build_model
from .models import LocalModel, summarize_actionable, stage_zero


_EVENT_FIELDS = {
    "message_id", "change_seq", "timestamp", "display_name", "text", "reply_to",
    "quoted_message_id", "referenced_ids",
}
_FORBIDDEN_EVENT_FIELDS = {
    "participant", "chat_jid", "target_group_jid", "sender", "recipient", "remote_jid",
}
_EXPECTED_FIELDS = {
    "required_atoms", "optional_atoms", "answered_question_ids", "unanswered_ids",
    "limited_update_question_ids", "forbidden_unsupported_atoms",
    "forbidden_superseded_atoms", "privacy_sentinels",
}
_URL = re.compile(r"https?://[^\s]+")


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be nonempty trimmed text")
    return value


def _text_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a list of nonempty text")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return value


def _validate_public_urls(text: str) -> None:
    for raw_url in _URL.findall(text):
        url = raw_url.rstrip(".,;")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.hostname.endswith(".invalid"):
            raise ValueError("public fixture URL must use an explicit .invalid host")


def load_corpus(path: Path) -> dict[str, Any]:
    """Load the publication-safe one-pass corpus and reject operational metadata."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"schema_version", "description", "coverage", "windows"}:
        raise ValueError("one-pass corpus schema drift")
    if data["schema_version"] != 1:
        raise ValueError("unsupported one-pass corpus schema")
    description = _nonempty_text(data["description"], "description")
    if "synthetic" not in description.casefold() or "invented" not in description.casefold():
        raise ValueError("corpus must explicitly declare synthetic invented content")
    coverage = _text_list(data["coverage"], "coverage")
    windows = data["windows"]
    if not isinstance(windows, list) or not windows:
        raise ValueError("corpus windows must be a nonempty list")
    seen_window_ids: set[str] = set()
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"id", "material", "events", "expected"}:
            raise ValueError("evaluation window schema drift")
        window_id = _nonempty_text(window["id"], "window id")
        if window_id in seen_window_ids:
            raise ValueError("duplicate evaluation window id")
        seen_window_ids.add(window_id)
        if not isinstance(window["material"], bool):
            raise ValueError("window material flag must be boolean")
        events = window["events"]
        if not isinstance(events, list) or not events:
            raise ValueError("evaluation window events must be nonempty")
        message_ids: set[str] = set()
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("evaluation event must be an object")
            forbidden = set(event) & _FORBIDDEN_EVENT_FIELDS
            unknown = set(event) - _EVENT_FIELDS
            if forbidden or unknown:
                names = sorted(forbidden | unknown)
                raise ValueError(f"forbidden event field: {', '.join(names)}")
            message_id = _nonempty_text(event.get("message_id"), "message_id")
            if message_id in message_ids:
                raise ValueError("message IDs must be unique within a window")
            message_ids.add(message_id)
            if not isinstance(event.get("change_seq"), int) or isinstance(event.get("change_seq"), bool) or event["change_seq"] < 1:
                raise ValueError("change_seq must be a positive integer")
            text = _nonempty_text(event.get("text"), "event text")
            _validate_public_urls(text)
        expected = window["expected"]
        if not isinstance(expected, dict) or set(expected) != _EXPECTED_FIELDS:
            raise ValueError("evaluation expectation schema drift")
        for field in _EXPECTED_FIELDS:
            _text_list(expected[field], field)
        classified_ids = (
            expected["answered_question_ids"]
            + expected["unanswered_ids"]
            + expected["limited_update_question_ids"]
        )
        if len(classified_ids) != len(set(classified_ids)) or any(item not in message_ids for item in classified_ids):
            raise ValueError("resolution expectations must uniquely reference window messages")
        source_blob = "\n".join(str(event["text"]) for event in events)
        if any(sentinel not in source_blob for sentinel in expected["privacy_sentinels"]):
            raise ValueError("privacy sentinel must occur in its synthetic source window")
    return {**data, "coverage": coverage, "windows": windows}


def evaluation_items(window: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Apply the same deterministic one-pass pre-model normalization as the runner."""
    return stage_zero(window["events"])


def _source_ref_by_message_id(window: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["message_id"]): f"S{index:03d}"
        for index, item in enumerate(evaluation_items(window), start=1)
    }


def _topic_question_refs(response: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    answered: set[str] = set()
    limited: set[str] = set()
    topics = response.get("topics")
    if not isinstance(topics, list):
        return answered, limited
    for topic in topics:
        if not isinstance(topic, Mapping):
            continue
        reference = topic.get("question_source_ref")
        if isinstance(reference, str):
            if topic.get("limitation"):
                limited.add(reference)
            else:
                answered.add(reference)
    return answered, limited


def _unanswered_refs(response: Mapping[str, Any]) -> set[str]:
    unanswered = response.get("unanswered")
    if not isinstance(unanswered, list):
        return set()
    return {
        reference
        for item in unanswered
        if isinstance(item, Mapping) and isinstance((reference := item.get("question_source_ref")), str)
    }


def score_candidate(
    window: Mapping[str, Any],
    response: Mapping[str, Any],
    rendered: str,
    prompt: str,
) -> dict[str, Any]:
    """Score automatable gates; unsupported factual-title review remains human."""
    expected = window["expected"]
    required = expected["required_atoms"]
    optional = expected["optional_atoms"]
    missing_required = [atom for atom in required if atom not in rendered]
    missing_optional = [atom for atom in optional if atom not in rendered]
    folded_rendered = rendered.casefold()
    known_unsupported = [
        atom for atom in expected["forbidden_unsupported_atoms"]
        if atom.casefold() in folded_rendered
    ]
    superseded = [
        atom for atom in expected["forbidden_superseded_atoms"]
        if atom.casefold() in folded_rendered
    ]
    leak_surface = prompt + "\n" + rendered
    privacy_leaks = [
        sentinel for sentinel in expected["privacy_sentinels"]
        if sentinel.casefold() in leak_surface.casefold()
    ]

    references = _source_ref_by_message_id(window)
    actual_answered, actual_limited = _topic_question_refs(response)
    actual_unanswered = _unanswered_refs(response)
    classification_errors: list[str] = []
    expected_answered = {references.get(message_id) for message_id in expected["answered_question_ids"]}
    expected_limited = {references.get(message_id) for message_id in expected["limited_update_question_ids"]}
    expected_unanswered = {references.get(message_id) for message_id in expected["unanswered_ids"]}
    for message_id in expected["answered_question_ids"]:
        reference = references.get(message_id)
        if reference not in actual_answered or reference in actual_unanswered:
            classification_errors.append(f"{message_id}:answered")
    for message_id in expected["limited_update_question_ids"]:
        reference = references.get(message_id)
        if reference not in actual_limited or reference in actual_unanswered:
            classification_errors.append(f"{message_id}:update_with_limitation")
    for message_id in expected["unanswered_ids"]:
        reference = references.get(message_id)
        if reference not in actual_unanswered or reference in actual_answered | actual_limited:
            classification_errors.append(f"{message_id}:unanswered")
    expected_classified = expected_answered | expected_limited | expected_unanswered
    for reference in sorted((actual_answered | actual_limited | actual_unanswered) - expected_classified):
        classification_errors.append(f"{reference}:unexpected_resolution_entry")

    return {
        "required_total": len(required),
        "missing_required_atoms": missing_required,
        "missing_required_count": len(missing_required),
        "optional_total": len(optional),
        "missing_optional_atoms": missing_optional,
        "missing_optional_count": len(missing_optional),
        "classification_errors": classification_errors,
        "classification_error_count": len(classification_errors),
        "privacy_leak_count": len(privacy_leaks),
        "known_unsupported_count": len(known_unsupported),
        "supersession_error_count": len(superseded),
    }


def _two_call_scoring_response(
    extraction: str, reconciliation: str, items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project an accepted structural plan onto the existing synthetic rubric."""
    atoms = {atom["atom_ref"]: atom for atom in extract_evidence(extraction, items)}
    plan = json.loads(reconciliation)
    return {
        "topics": [
            {
                "question_source_ref": (
                    atoms[entry["question_atom_ref"]]["source_ref"]
                    if entry.get("question_atom_ref") else None
                ),
                "limitation": entry.get("limitation_atom_refs") or [],
            }
            for entry in plan.get("topics", [])
            if isinstance(entry, Mapping)
        ],
        "unanswered": [
            {"question_source_ref": atoms[entry["atom_ref"]]["source_ref"]}
            for entry in plan.get("unanswered", [])
            if isinstance(entry, Mapping) and entry.get("atom_ref") in atoms
        ],
    }


class _RecordingModel:
    def __init__(self, role: str, delegate: LocalModel) -> None:
        self.role = role
        self.delegate = delegate
        self.prompts: list[str] = []
        self.responses: list[str] = []
        self.errors: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def complete_structured(self, prompt: str, schema: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        try:
            structured = getattr(self.delegate, "complete_structured", None)
            response = structured(prompt, schema) if callable(structured) else self.delegate.complete(prompt)
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            raise
        self.responses.append(response)
        return response


def _write_owner_only_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("evaluation artifact directory must be owner-only")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def evaluate_window(
    window: Mapping[str, Any],
    run_index: int,
    final_model: LocalModel,
    fallback_model: LocalModel,
    *,
    pipeline_mode: str = "one_pass",
    final_instruction: str = "",
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    """Execute one isolated candidate window and return only sanitized metrics.

    Detailed prompts, raw candidates, validation errors, and the rendered digest
    are written only to the explicitly supplied owner-only artifact path.
    """
    if not isinstance(run_index, int) or isinstance(run_index, bool) or run_index < 1:
        raise ValueError("run_index must be a positive integer")
    items = evaluation_items(window)
    if pipeline_mode not in {"one_pass", "two_call"}:
        raise ValueError("evaluation pipeline_mode must be one_pass or two_call")
    final_role, fallback_role = (
        ("extractor", "reconciler") if pipeline_mode == "two_call" else ("final", "fallback")
    )
    final = _RecordingModel(final_role, final_model)
    fallback = _RecordingModel(fallback_role, fallback_model)
    rendered = ""
    response_data: Mapping[str, Any] | None = None
    failure_category: str | None = None
    failure_detail: str | None = None
    try:
        summarize = summarize_ephemeral_two_call if pipeline_mode == "two_call" else summarize_actionable
        result = summarize(
            items, final, fallback, False, final_instruction=final_instruction,
        )
        rendered = result.text
        if pipeline_mode == "two_call" and rendered and final.responses and fallback.responses:
            response_data = _two_call_scoring_response(
                final.responses[-1], fallback.responses[-1], items,
            )
        elif rendered:
            selected_responses = final.responses if result.model == "final" else fallback.responses
            if selected_responses:
                decoded = json.loads(selected_responses[-1])
                if not isinstance(decoded, Mapping):
                    raise ValueError("accepted candidate must decode to an object")
                response_data = decoded
    except Exception as exc:
        failure_category = type(exc).__name__
        failure_detail = str(exc)

    accepted = bool(rendered and response_data is not None)
    prompts = final.prompts + fallback.prompts
    score = score_candidate(
        window, response_data or {}, rendered, "\n".join(prompts),
    )
    outcome = {
        "window_id": str(window["id"]),
        "run_index": run_index,
        "material": bool(window["material"]),
        "accepted": accepted,
        "model_calls": final.calls + fallback.calls,
        "repair_or_fallback_used": (
            final.calls + fallback.calls > 2 if pipeline_mode == "two_call" else fallback.calls > 0
        ),
        "failure_category": failure_category,
        "score": score,
    }
    if artifact_path is not None:
        _write_owner_only_json(artifact_path, {
            "window_id": str(window["id"]),
            "run_index": run_index,
            "prompts": [
                *({"role": final_role, "text": prompt} for prompt in final.prompts),
                *({"role": fallback_role, "text": prompt} for prompt in fallback.prompts),
            ],
            "raw_responses": [
                *({"role": final_role, "text": response} for response in final.responses),
                *({"role": fallback_role, "text": response} for response in fallback.responses),
            ],
            "model_error_categories": [
                *({"role": final_role, "category": error} for error in final.errors),
                *({"role": fallback_role, "category": error} for error in fallback.errors),
            ],
            "validation_failure_detail": failure_detail,
            "rendered_digest": rendered,
            "outcome": outcome,
        })
    return outcome


def evaluate_corpus(
    corpus: Mapping[str, Any],
    final_model: LocalModel,
    fallback_model: LocalModel,
    artifact_dir: Path,
    *,
    repeats: int = 2,
    pipeline_mode: str = "one_pass",
    final_instruction: str = "",
    window_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Run the exploratory corpus while keeping detailed evidence owner-only."""
    if repeats != 2:
        raise ValueError("Phase 2 exploratory evaluation requires exactly two runs per window")
    repository_root = Path(__file__).resolve().parents[1]
    resolved_artifacts = artifact_dir.resolve()
    if resolved_artifacts == repository_root or repository_root in resolved_artifacts.parents:
        raise ValueError("detailed evaluation artifacts must remain outside the repository")
    windows = list(corpus["windows"])
    if window_ids is not None:
        known_ids = {str(window["id"]) for window in windows}
        if not window_ids or not window_ids <= known_ids:
            raise ValueError("focused evaluation window IDs must be nonempty and known")
        windows = [window for window in windows if window["id"] in window_ids]
    artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(artifact_dir, 0o700)
    results: list[dict[str, Any]] = []
    sequence = 0
    for window in windows:
        for run_index in range(1, repeats + 1):
            sequence += 1
            evidence_path = artifact_dir / f"run-{sequence:02d}-{window['id']}-r{run_index}.json"
            results.append(evaluate_window(
                window, run_index, final_model, fallback_model,
                pipeline_mode=pipeline_mode, final_instruction=final_instruction,
                artifact_path=evidence_path,
            ))
    _write_owner_only_json(artifact_dir / "results.json", {
        "schema_version": 1,
        "expected_runs": len(windows) * repeats,
        "reliability_interpretation": "exploratory_repeatability_only",
        "results": results,
    })
    return results


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def aggregate_results(
    results: Sequence[Mapping[str, Any]],
    manual_reviews: Mapping[str, Mapping[str, Any]],
    *,
    expected_runs: int,
    normal_calls: int = 1,
    maximum_calls: int = 2,
) -> dict[str, Any]:
    """Build a publication-safe aggregate without prompts, responses, or digests."""
    material = [result for result in results if result["material"]]
    nonmaterial = [result for result in results if not result["material"]]
    accepted = [result for result in results if result["accepted"]]
    required_total = sum((result.get("score") or {}).get("required_total", 0) for result in material)
    missing_required = sum((result.get("score") or {}).get("missing_required_count", 0) for result in material)
    optional_total = sum((result.get("score") or {}).get("optional_total", 0) for result in material)
    missing_optional = sum((result.get("score") or {}).get("missing_optional_count", 0) for result in material)
    classification_errors = sum((result.get("score") or {}).get("classification_error_count", 0) for result in material)
    # A sentinel can reach the projected prompt even when the candidate is rejected.
    privacy_leaks = sum((result.get("score") or {}).get("privacy_leak_count", 0) for result in results)
    known_unsupported = sum((result.get("score") or {}).get("known_unsupported_count", 0) for result in accepted)
    supersession_errors = sum((result.get("score") or {}).get("supersession_error_count", 0) for result in accepted)
    required_review_keys = {
        f"{result['window_id']}:{result['run_index']}" for result in accepted
    }
    reviews_complete = required_review_keys <= set(manual_reviews)
    manual_unsupported = sum(
        int(manual_reviews[key].get("unsupported_accepted_count", 0))
        for key in required_review_keys & set(manual_reviews)
    )
    material_retry_count = sum(int(result["model_calls"] > normal_calls) for result in material)
    material_failure_count = sum(int(not result["accepted"]) for result in material)
    nonmaterial_accepted_count = sum(int(result["accepted"]) for result in nonmaterial)
    completed = len(results) == expected_runs
    retry_rate = _ratio(material_retry_count, len(material))
    automated_pass = all((
        completed,
        missing_required == 0,
        classification_errors == 0,
        privacy_leaks == 0,
        known_unsupported == 0,
        supersession_errors == 0,
        material_failure_count == 0,
        retry_rate <= 0.15,
        nonmaterial_accepted_count == 0,
    ))
    if not automated_pass:
        decision = "not_accepted"
    elif not reviews_complete:
        decision = "pending_manual_review"
    elif manual_unsupported:
        decision = "not_accepted"
    else:
        decision = "accepted"
    return {
        "evaluation_runs": len(results),
        "expected_runs": expected_runs,
        "model_invocations": sum(int(result["model_calls"]) for result in results),
        "maximum_model_invocations": expected_runs * maximum_calls,
        "reliability_interpretation": "exploratory_repeatability_only",
        "material_runs": len(material),
        "material_retry_count": material_retry_count,
        "material_retry_rate": retry_rate,
        "material_failure_count": material_failure_count,
        "material_failure_rate": _ratio(material_failure_count, len(material)),
        "required_atom_recall": 1.0 - _ratio(missing_required, required_total),
        "optional_atom_recall": 1.0 - _ratio(missing_optional, optional_total),
        "classification_error_count": classification_errors,
        "privacy_leak_count": privacy_leaks,
        "known_unsupported_count": known_unsupported,
        "manual_unsupported_accepted_count": manual_unsupported,
        "manual_review_complete": reviews_complete,
        "supersession_error_count": supersession_errors,
        "nonmaterial_accepted_count": nonmaterial_accepted_count,
        "nonmaterial_fail_closed_count": sum(int(not result["accepted"]) for result in nonmaterial),
        "model_quality_decision": decision,
    }


def load_evaluation_models(policy_path: Path):
    """Build models only through the repository's validated config/provider path."""
    config = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    if not config.example_only:
        raise ValueError("quality evaluation requires a publication-safe example-only policy")
    if (
        config.models.pipeline_mode not in {"one_pass", "two_call"}
        or config.models.provider not in {"hermes-openai-codex", "github-copilot"}
    ):
        raise ValueError("quality evaluation requires an actionable explicit Hermes CLI provider")
    if config.models.endpoint != "local://hermes-cli":
        raise ValueError("quality evaluation requires the local Hermes CLI connector")
    if (
        (config.models.final, config.models.fallback) != ("gpt-5.6-terra", "gpt-5.6-terra")
        or config.models.reasoning_effort != "high"
    ):
        raise ValueError("Phase 2 evaluation requires gpt-5.6-terra with high reasoning")
    return config, build_model(config.models, "final"), build_model(config.models, "fallback")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated synthetic digest quality evaluation")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--window", action="append", dest="window_ids",
        help="run only this corpus window; repeat for a focused set",
    )
    parser.add_argument(
        "--invoke-model", action="store_true",
        help="required acknowledgement that this command invokes Hermes; it is not operator authorization",
    )
    args = parser.parse_args(argv)
    if not args.invoke_model:
        parser.error("--invoke-model is required; obtain exact operator approval before using it")
    corpus = load_corpus(args.corpus)
    config, final_model, fallback_model = load_evaluation_models(args.policy)
    window_ids = set(args.window_ids) if args.window_ids else None
    results = evaluate_corpus(
        corpus, final_model, fallback_model, args.artifact_dir,
        pipeline_mode=config.models.pipeline_mode,
        final_instruction=config.models.final_instruction,
        window_ids=window_ids,
    )
    selected = [
        window for window in corpus["windows"]
        if window_ids is None or window["id"] in window_ids
    ]
    summary = aggregate_results(
        results, {}, expected_runs=len(selected) * 2,
        normal_calls=2 if config.models.pipeline_mode == "two_call" else 1,
        maximum_calls=3 if config.models.pipeline_mode == "two_call" else 2,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
