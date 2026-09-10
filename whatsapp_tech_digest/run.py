from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .config import DigestConfig
from .model_provider import build_model
from .models import ModelFailure, high_recall_select, stage_zero, summarize_actionable, summarize_selected
from .smtp_delivery import send
from .spool import DeliveryBlockedError, DurableSpool


_EXECUTION_MODES = frozenset({"validate-only", "render-only", "delivery-capable"})


def _execution_mode(value: str) -> str:
    if value not in _EXECUTION_MODES:
        raise ValueError(f"execution_mode must be one of: {', '.join(sorted(_EXECUTION_MODES))}")
    return value


def _config_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_review_manifest(path: Path, review_id: str, output: str) -> None:
    """Create an owner-only, raw-content-free manifest for one reviewed render."""
    if path.exists():
        raise FileExistsError("review manifest already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise PermissionError("review manifest parent must be owner-only")
    payload = json.dumps({
        "schema_version": "reviewed-artifact-manifest-v1",
        "review_id": review_id,
        "content_hash": hashlib.sha256(output.encode()).hexdigest(),
    }, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _read_review_manifest(path: Path, output: str) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryBlockedError("review manifest is unavailable or invalid") from exc
    if not isinstance(data, dict) or set(data) != {"schema_version", "review_id", "content_hash"}:
        raise DeliveryBlockedError("review manifest schema drift")
    if data["schema_version"] != "reviewed-artifact-manifest-v1" or not isinstance(data["review_id"], str) or not data["review_id"]:
        raise DeliveryBlockedError("review manifest identity is invalid")
    if data["content_hash"] != hashlib.sha256(output.encode()).hexdigest():
        raise DeliveryBlockedError("review manifest body hash does not match")
    return data["review_id"]


def deliver_reviewed(policy_path: Path, spool_path: Path, artifact_path: Path, manifest_path: Path) -> str:
    """Send only the exact reviewed artifact; no model construction is permitted."""
    config = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    config.require_live_safe()
    try:
        output = artifact_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeliveryBlockedError("reviewed artifact is unavailable") from exc
    review_id = _read_review_manifest(manifest_path, output)
    spool = DurableSpool(spool_path, config.target_group_jid)
    try:
        review = spool.reviewed_artifact(review_id, output, config_hash=_config_hash(policy_path))
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
        }
        if state in {"accepted", "unknown"}:
            final_kwargs["provenance"] = review["provenance"]
        spool.record_run(snapshot, review["run_type"], state, output=output, **final_kwargs)
        return state
    finally:
        spool.close()


def generate(policy_path: Path, spool_path: Path, *, execution_mode: str = "validate-only", review_manifest: Path | None = None) -> str:
    execution_mode = _execution_mode(execution_mode)
    if review_manifest is not None and execution_mode != "render-only":
        raise ValueError("review manifests are permitted only for render-only execution")
    config = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    if execution_mode == "delivery-capable":
        config.require_live_safe()
    spool = DurableSpool(spool_path, config.target_group_jid)
    snapshot = spool.snapshot()
    events, run_type, omission = spool.pending_events(snapshot)
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
    spool.require_current(events)
    normalized = stage_zero(events)
    if execution_mode == "validate-only":
        return ""
    if config.models.pipeline_mode == "one_pass":
        selected = [
            item for item in normalized
            if not item.get("untrusted_policy_override") and not item.get("mechanical_ack")
        ]
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
    spool.require_current(selected)
    try:
        summarize = summarize_actionable if config.models.pipeline_mode == "one_pass" else summarize_selected
        result = summarize(
            selected,
            build_model(config.models, "final"),
            build_model(config.models, "fallback"),
            degraded,
            final_instruction=config.models.final_instruction,
        )
    except ModelFailure:
        if execution_mode == "delivery-capable":
            spool.record_run(snapshot, run_type, "failed", omission_note=omission, candidate_count=len(selected), source_count=len(events), config_hash=_config_hash(policy_path), coverage_snapshot=json.dumps(spool.coverage()))
        raise
    if execution_mode == "render-only":
        if review_manifest is not None:
            if not result.text or result.provenance is None:
                raise DeliveryBlockedError("an empty or unprovenanced render cannot become a reviewed artifact")
            review_id = spool.record_reviewed_artifact(
                snapshot, run_type, result.text, candidate_count=len(result.included_message_ids),
                source_count=len(events), model_id=result.model, config_hash=_config_hash(policy_path),
                coverage_snapshot=json.dumps(spool.coverage()), provenance=result.provenance,
            )
            _write_review_manifest(review_manifest, review_id, result.text)
        return result.text
    if not result.text:
        spool.record_run(snapshot, run_type, "empty", omission_note=omission, candidate_count=0, source_count=len(events), model_id=result.model, config_hash=_config_hash(policy_path), coverage_snapshot=json.dumps(spool.coverage()))
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
    parser.add_argument("--review-manifest", type=Path, help="owner-only revision-only manifest for a render-only artifact")
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
        generate_kwargs = {"review_manifest": args.review_manifest} if args.review_manifest is not None else {}
        output = generate(args.policy, args.spool, execution_mode=args.execution_mode, **generate_kwargs)
    if args.execution_mode == "render-only" and output:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())