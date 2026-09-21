from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from .config import DigestConfig
from .ephemeral_two_call import summarize_ephemeral_two_call
from .model_provider import build_model
from .models import ModelFailure, high_recall_select, stage_zero, summarize_actionable, summarize_selected
from .smtp_delivery import send
from .spool import DeliveryBlockedError, DurableSpool


_EXECUTION_MODES = frozenset({"validate-only", "render-only", "delivery-capable"})
_HISTORICAL_LABEL = (
    "Bounded historical backlog window\n\n"
    "Later changes remain pending and may correct or supersede this material. "
    "Human review is required before delivery.\n\n"
)
_PORTABLE_ENVELOPE_FIELDS = {
    "schema_version", "review_id", "artifact_hash", "policy_hash", "target_binding",
    "checkpoint_seq", "cutoff_seq", "window_kind", "run_type", "model_id",
    "candidate_count", "source_count", "coverage_metadata",
    "provenance_schema_version", "provenance", "provenance_hash", "envelope_hash",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _execution_mode(value: str) -> str:
    if value not in _EXECUTION_MODES:
        raise ValueError(f"execution_mode must be one of: {', '.join(sorted(_EXECUTION_MODES))}")
    return value


def _config_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _target_binding(target_group_jid: str) -> str:
    return hashlib.sha256(f"whatsapp-tech-digest-target-v1:{target_group_jid}".encode()).hexdigest()


def _portable_envelope_hash(envelope: dict[str, object]) -> str:
    unsigned = {key: value for key, value in envelope.items() if key != "envelope_hash"}
    return hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest()


def _owner_only_path(path: Path, *, must_exist: bool) -> None:
    try:
        parent = path.parent
        if not parent.exists():
            if must_exist:
                raise FileNotFoundError(path)
            parent.mkdir(parents=True, mode=0o700)
        parent_metadata = parent.stat()
        if parent_metadata.st_uid != os.geteuid() or stat.S_IMODE(parent_metadata.st_mode) & 0o077:
            raise PermissionError("review bundle parent must be owner-only")
        if must_exist:
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise PermissionError("review bundle file must be a regular owner-only file")
    except OSError:
        raise


def _write_owner_only(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError("review bundle file already exists")
    _owner_only_path(path, must_exist=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _write_review_manifest(
    path: Path,
    review_facts: dict[str, object],
    *,
    target_group_jid: str,
) -> None:
    """Create an owner-only portable, revision-only envelope for one render."""
    envelope = {
        "schema_version": "portable-review-envelope-v1",
        **review_facts,
        "target_binding": _target_binding(target_group_jid),
    }
    envelope["envelope_hash"] = _portable_envelope_hash(envelope)
    _write_owner_only(path, _canonical_json(envelope) + "\n")


def _read_review_manifest(
    path: Path, output_bytes: bytes, *, target_group_jid: str
) -> dict[str, object]:
    try:
        _owner_only_path(path, must_exist=True)
        raw_envelope = path.read_bytes()
        data = json.loads(raw_envelope.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryBlockedError("portable review envelope is unavailable or invalid") from exc
    if not isinstance(data, dict) or set(data) != _PORTABLE_ENVELOPE_FIELDS:
        raise DeliveryBlockedError("portable review envelope schema drift")
    string_fields = (
        "review_id", "artifact_hash", "policy_hash", "target_binding", "window_kind",
        "run_type", "provenance_schema_version", "provenance_hash", "envelope_hash",
    )
    if (
        data["schema_version"] != "portable-review-envelope-v1"
        or any(not isinstance(data[field], str) or not data[field] for field in string_fields)
        or not isinstance(data["model_id"], str) or not data["model_id"]
        or any(
            not isinstance(data[field], int) or isinstance(data[field], bool) or data[field] < 0
            for field in ("checkpoint_seq", "cutoff_seq", "candidate_count", "source_count")
        )
        or not isinstance(data["coverage_metadata"], dict)
        or not isinstance(data["provenance"], dict)
    ):
        raise DeliveryBlockedError("portable review envelope values are invalid")
    if any(
        not _SHA256.fullmatch(data[field])
        for field in (
            "review_id", "artifact_hash", "policy_hash", "target_binding",
            "provenance_hash", "envelope_hash",
        )
    ):
        raise DeliveryBlockedError("portable review envelope digest is invalid")
    coverage = data["coverage_metadata"]
    gaps = coverage.get("gaps") if isinstance(coverage, dict) else None
    if (
        set(coverage) != {"observed_events", "collector_state", "gaps"}
        or not isinstance(coverage["observed_events"], int)
        or isinstance(coverage["observed_events"], bool)
        or coverage["observed_events"] < 0
        or not isinstance(coverage["collector_state"], str)
        or not isinstance(gaps, list)
        or any(
            not isinstance(gap, dict)
            or set(gap) != {
                "id", "state", "started_at", "ended_at", "reason",
                "guard_active", "observed_events",
            }
            or not isinstance(gap["id"], int)
            or isinstance(gap["id"], bool)
            or not isinstance(gap["state"], str)
            or not isinstance(gap["started_at"], str)
            or gap["ended_at"] is not None and not isinstance(gap["ended_at"], str)
            or not isinstance(gap["reason"], str)
            or not isinstance(gap["guard_active"], bool)
            or not isinstance(gap["observed_events"], int)
            or isinstance(gap["observed_events"], bool)
            for gap in gaps
        )
        or data["candidate_count"] > data["source_count"]
        or data["window_kind"] not in {"complete", "bounded-historical-backlog"}
        or data["run_type"] not in {"first-run", "normal", "catch-up", "bounded-backlog"}
    ):
        raise DeliveryBlockedError("portable review coverage or count metadata is invalid")
    if data["envelope_hash"] != _portable_envelope_hash(data):
        raise DeliveryBlockedError("portable review envelope hash does not match")
    if data["target_binding"] != _target_binding(target_group_jid):
        raise DeliveryBlockedError("portable review target binding does not match")
    if raw_envelope != (_canonical_json(data) + "\n").encode("utf-8"):
        raise DeliveryBlockedError("portable review envelope encoding is not canonical")
    if data["artifact_hash"] != hashlib.sha256(output_bytes).hexdigest():
        raise DeliveryBlockedError("portable review artifact hash does not match")
    return data


def _merge_carried_events(
    events: list[dict[str, Any]], carried: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not events:
        return events
    pending_revisions = {(str(item["message_id"]), int(item["change_seq"])) for item in events}
    tracked_revisions = {(str(item["message_id"]), int(item["change_seq"])) for item in carried}
    window_timestamps = [item.get("timestamp") for item in events if item.get("timestamp")]
    return [
        *[
            {
                **item,
                "carried_forward": True,
                "tracked_item": True,
                "digest_window_timestamps": window_timestamps,
            }
            for item in carried
            if (str(item["message_id"]), int(item["change_seq"])) not in pending_revisions
        ],
        *[
            {
                **item,
                "tracked_item": (
                    str(item["message_id"]), int(item["change_seq"])
                ) in tracked_revisions,
            }
            for item in events
        ],
    ]


def _live_window_facts(
    spool: DurableSpool, snapshot: dict[str, int], *, bounded: bool
) -> dict[str, Any]:
    """Recompute the window facts an envelope must match, using no model and no age horizon."""
    events, _run_type, _omission = spool.pending_events(snapshot, include_historical_prefix=True)
    carried = spool.carry_forward_events(snapshot, allow_later_changes=bounded)
    merged = _merge_carried_events(events, carried)
    return {
        "source_count": len(merged),
        "candidate_count": len(stage_zero(merged)),
        "run_type": (
            "bounded-backlog"
            if bounded
            else ("first-run" if int(snapshot["checkpoint_seq"]) == 0 else "normal")
        ),
    }


def deliver_reviewed(policy_path: Path, spool_path: Path, artifact_path: Path, manifest_path: Path) -> str:
    """Send only the exact reviewed artifact; no model construction is permitted."""
    config = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    config.require_live_safe()
    config.require_production_pipeline()
    try:
        _owner_only_path(artifact_path, must_exist=True)
        output_bytes = artifact_path.read_bytes()
        output = output_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DeliveryBlockedError("reviewed artifact is unavailable") from exc
    envelope = _read_review_manifest(
        manifest_path, output_bytes, target_group_jid=config.target_group_jid
    )
    spool = DurableSpool(spool_path, config.target_group_jid)
    try:
        review = spool.consume_portable_review(
            envelope,
            output,
            config_hash=_config_hash(policy_path),
            expected_window=_live_window_facts(
                spool,
                {
                    "checkpoint_seq": int(envelope["checkpoint_seq"]),
                    "cutoff_seq": int(envelope["cutoff_seq"]),
                },
                bounded=envelope["window_kind"] == "bounded-historical-backlog",
            ),
        )
        snapshot = review["snapshot"]
        digest_id = spool.record_run(
            snapshot, review["run_type"], "failed", candidate_count=review["candidate_count"],
            source_count=review["source_count"], model_id=review["model_id"],
            config_hash=review["config_hash"], coverage_snapshot=review["coverage_snapshot"],
        )
        subject = f"[TechTeam Daily] {datetime.now(ZoneInfo('America/Toronto')).date().isoformat()} — {review['candidate_count']} material update(s)"
        state = send(config.smtp, digest_id, subject, output)
        final_kwargs = {
            "candidate_count": review["candidate_count"], "source_count": review["source_count"],
            "model_id": review["model_id"], "config_hash": review["config_hash"],
            "coverage_snapshot": review["coverage_snapshot"],
            "allow_later_changes": review["window_kind"] == "bounded-historical-backlog",
        }
        if state in {"accepted", "unknown"}:
            final_kwargs["provenance"] = review["provenance"]
        spool.record_run(snapshot, review["run_type"], state, output=output, **final_kwargs)
        return state
    finally:
        spool.close()


def generate(
    policy_path: Path,
    spool_path: Path,
    *,
    execution_mode: str = "validate-only",
    review_manifest: Path | None = None,
    review_artifact: Path | None = None,
    bounded_cutoff: int | None = None,
) -> str:
    execution_mode = _execution_mode(execution_mode)
    if review_manifest is not None and execution_mode != "render-only":
        raise ValueError("review manifests are permitted only for render-only execution")
    if review_artifact is not None and (execution_mode != "render-only" or review_manifest is None):
        raise ValueError("review artifact output requires a render-only review envelope")
    if bounded_cutoff is not None and (
        execution_mode != "render-only" or review_manifest is None or review_artifact is None
    ):
        raise ValueError(
            "bounded-prefix recovery is operator-only and requires render-only artifact and envelope paths"
        )
    config = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    if execution_mode == "delivery-capable":
        config.require_live_safe()
    if execution_mode == "delivery-capable" or review_manifest is not None:
        config.require_production_pipeline()
    spool = DurableSpool(spool_path, config.target_group_jid)
    snapshot = spool.snapshot(cutoff_seq=bounded_cutoff)
    if bounded_cutoff is None:
        events, run_type, omission = spool.pending_events(snapshot)
    else:
        events, run_type, omission = spool.pending_events(
            snapshot, include_historical_prefix=True
        )
    if omission:
        omission_checkpoint = spool.omission_prefix_checkpoint(snapshot)
        if execution_mode == "delivery-capable":
            spool.record_run(
                snapshot,
                run_type,
                "failed",
                omission_note=omission,
                omission_checkpoint_seq=-1 if omission_checkpoint is None else omission_checkpoint,
                source_count=len(events),
                config_hash=_config_hash(policy_path),
                coverage_snapshot=json.dumps(spool.coverage()),
            )
        raise DeliveryBlockedError("durable omissions must be explicitly reconciled before model processing or SMTP")
    if bounded_cutoff is None:
        carried = spool.carry_forward_events(snapshot)
    else:
        carried = spool.carry_forward_events(snapshot, allow_later_changes=True)
    if carried and config.models.pipeline_mode not in {"one_pass", "two_call"}:
        raise DeliveryBlockedError("cross-day question state requires the one-pass actionable pipeline")
    tracked_revisions = {(str(item["message_id"]), int(item["change_seq"])) for item in carried}
    events = _merge_carried_events(events, carried)
    if bounded_cutoff is None:
        spool.require_current(events)
    else:
        spool.require_current(events, cutoff_seq=bounded_cutoff)
    normalized = stage_zero(events)
    if execution_mode == "validate-only":
        return ""
    if config.models.pipeline_mode in {"one_pass", "two_call"}:
        selected = list(normalized)
        selected_revisions = {
            (str(item["message_id"]), int(item["change_seq"])) for item in selected
        }
        if events and tracked_revisions - selected_revisions:
            raise DeliveryBlockedError(
                "a tracked revision was removed by deterministic normalization; "
                "correct or revoke that source revision before retrying"
            )
        degraded = False
    else:
        selected, degraded = high_recall_select(
            normalized,
            build_model(config.models, "preclassifier"),
            config.models.noise_threshold,
            batch_size=config.models.batch_size,
            spool=spool,
            model_id=config.models.preclassifier,
            classifier_instruction=config.models.classifier_instruction,
        )
    if bounded_cutoff is None:
        spool.require_current(selected)
    else:
        spool.require_current(selected, cutoff_seq=bounded_cutoff)
    try:
        summarize = {
            "one_pass": summarize_actionable,
            "two_call": summarize_ephemeral_two_call,
        }.get(config.models.pipeline_mode, summarize_selected)
        final_model = build_model(config.models, "final")
        fallback_model = (
            final_model
            if config.models.fallback == config.models.final
            else build_model(config.models, "fallback")
        )
        result = summarize(
            selected,
            final_model,
            fallback_model,
            degraded,
            final_instruction=config.models.final_instruction,
        )
    except ModelFailure:
        if execution_mode == "delivery-capable":
            spool.record_run(snapshot, run_type, "failed", omission_note=omission, candidate_count=len(selected), source_count=len(events), config_hash=_config_hash(policy_path), coverage_snapshot=json.dumps(spool.coverage()))
        raise
    if result.text and config.models.pipeline_mode in {"one_pass", "two_call"}:
        if result.provenance is None:
            raise DeliveryBlockedError("a rendered candidate requires validated provenance")
        spool.validate_provenance(
            snapshot,
            result.provenance,
            allow_later_changes=bounded_cutoff is not None,
        )
    if execution_mode == "render-only":
        if review_manifest is not None:
            if not result.text or result.provenance is None:
                raise DeliveryBlockedError("an empty or unprovenanced render cannot become a reviewed artifact")
            output = (
                _HISTORICAL_LABEL + result.text if bounded_cutoff is not None else result.text
            )
            review_id = spool.record_reviewed_artifact(
                snapshot, run_type, output, candidate_count=len(result.included_message_ids),
                source_count=len(events), model_id=result.model, config_hash=_config_hash(policy_path),
                coverage_snapshot=json.dumps(spool.coverage()), provenance=result.provenance,
                window_kind=(
                    "bounded-historical-backlog" if bounded_cutoff is not None else "complete"
                ),
            )
            if review_artifact is not None:
                _write_owner_only(review_artifact, output)
            facts = spool.portable_review_facts(review_id)
            _write_review_manifest(
                review_manifest, facts, target_group_jid=config.target_group_jid
            )
            return output
        return result.text
    if not result.text:
        spool.record_run(
            snapshot, run_type, "empty", omission_note=omission,
            candidate_count=0, source_count=len(events), model_id=result.model,
            config_hash=_config_hash(policy_path), coverage_snapshot=json.dumps(spool.coverage()),
            allow_active_empty=result.empty_validated,
        )
        return ""
    digest_id = spool.record_run(snapshot, run_type, "failed", omission_note=omission, candidate_count=len(result.included_message_ids), source_count=len(events), model_id=result.model, config_hash=_config_hash(policy_path), coverage_snapshot=json.dumps(spool.coverage()))
    spool.require_current(selected)
    subject = f"[TechTeam Daily] {datetime.now(ZoneInfo('America/Toronto')).date().isoformat()} — {len(result.included_message_ids)} material update(s)"
    state = send(config.smtp, digest_id, subject, result.text)
    spool.record_run(snapshot, run_type, state, output=result.text, omission_note=omission, candidate_count=len(result.included_message_ids), source_count=len(events), model_id=result.model, config_hash=_config_hash(policy_path), coverage_snapshot=json.dumps(spool.coverage()), provenance=result.provenance)
    if state != "accepted":
        raise RuntimeError("SMTP outcome requires operator reconciliation or retry; checkpoint retained")
    return ""


def _spool_from_args(args: argparse.Namespace) -> DurableSpool:
    config = DigestConfig.from_dict(json.loads(args.policy.read_text(encoding="utf-8")))
    return DurableSpool(args.spool, config.target_group_jid)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline WhatsApp digest pipeline and read-only state commands")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--spool", required=True, type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--execution-mode", choices=tuple(sorted(_EXECUTION_MODES)), default="validate-only")
    parser.add_argument("--review-manifest", type=Path, help="owner-only portable revision-only envelope for a render-only artifact")
    parser.add_argument("--review-artifact", type=Path, help="owner-only exact artifact output for a render-only review envelope")
    parser.add_argument("--bounded-cutoff", type=int, help="operator-selected durable cutoff for a bounded historical render")
    parser.add_argument("--deliver-reviewed-artifact", type=Path, help="exact reviewed artifact to deliver with --review-manifest")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--reconcile-digest-id")
    parser.add_argument("--reconcile-outcome", choices=("accepted", "failed"))
    parser.add_argument("--reconcile-message-id")
    parser.add_argument("--acknowledge-omission-digest-id")
    parser.add_argument("--acknowledge-omission-confirm")
    args = parser.parse_args(argv)
    if args.deliver_reviewed_artifact is not None:
        if args.execution_mode != "delivery-capable" or args.review_manifest is None:
            parser.error("--deliver-reviewed-artifact requires --execution-mode delivery-capable and --review-manifest")
    elif args.review_manifest is not None and args.execution_mode != "render-only":
        parser.error("--review-manifest requires --execution-mode render-only unless delivering a reviewed artifact")
    if args.review_artifact is not None and (
        args.execution_mode != "render-only" or args.review_manifest is None
    ):
        parser.error("--review-artifact requires render-only with --review-manifest")
    if args.bounded_cutoff is not None and (
        args.execution_mode != "render-only"
        or args.review_manifest is None
        or args.review_artifact is None
        or args.deliver_reviewed_artifact is not None
    ):
        parser.error(
            "--bounded-cutoff is operator-only and requires render-only with --review-manifest and --review-artifact"
        )
    if args.deliver_reviewed_artifact is not None and any((args.health, args.status, args.purge, args.reconcile_digest_id, args.acknowledge_omission_digest_id)):
        parser.error("--deliver-reviewed-artifact cannot be combined with another action")
    if sum(bool(value) for value in (args.health, args.status, args.purge, args.reconcile_digest_id, args.acknowledge_omission_digest_id)) > 1:
        parser.error("choose one action")
    if args.health or args.status or args.purge or args.reconcile_digest_id or args.acknowledge_omission_digest_id:
        if args.execution_mode != "validate-only":
            parser.error("--execution-mode applies only to a digest run")
        spool = _spool_from_args(args)
        if args.health:
            print(json.dumps(spool.coverage(), sort_keys=True))
        elif args.status:
            checkpoint = dict(spool.connection.execute("SELECT * FROM checkpoints WHERE id=1").fetchone())
            print(json.dumps({"checkpoint": checkpoint, "health": spool.coverage()}, sort_keys=True))
        elif args.purge:
            print(json.dumps({"purged": spool.purge()}, sort_keys=True))
        elif args.reconcile_digest_id:
            if not args.reconcile_outcome:
                parser.error("--reconcile-outcome is required with --reconcile-digest-id")
            spool.reconcile_delivery(args.reconcile_digest_id, args.reconcile_outcome, message_id=args.reconcile_message_id)
        else:
            if args.acknowledge_omission_confirm != "ACKNOWLEDGE_OMITTED_DURABLE_CHANGES":
                parser.error("--acknowledge-omission-confirm must exactly acknowledge omitted durable changes")
            spool.reconcile_omission(
                args.acknowledge_omission_digest_id,
                confirmation=args.acknowledge_omission_confirm,
            )
        return 0
    if args.lock is None:
        parser.error("--lock is required for a digest run")
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        if args.deliver_reviewed_artifact is not None:
            assert args.review_manifest is not None
            state = deliver_reviewed(args.policy, args.spool, args.deliver_reviewed_artifact, args.review_manifest)
            print(f"reviewed_delivery_state={state}")
            return 0 if state == "accepted" else 1
        generate_kwargs = {}
        if args.review_manifest is not None:
            generate_kwargs["review_manifest"] = args.review_manifest
        if args.review_artifact is not None:
            generate_kwargs["review_artifact"] = args.review_artifact
        if args.bounded_cutoff is not None:
            generate_kwargs["bounded_cutoff"] = args.bounded_cutoff
        output = generate(args.policy, args.spool, execution_mode=args.execution_mode, **generate_kwargs)
    if args.execution_mode == "render-only" and output:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
