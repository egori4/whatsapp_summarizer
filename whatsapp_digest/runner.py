"""Workflow runner for WhatsApp Technical Digest v2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import AppConfig, WorkflowConfig
from .database import DigestDatabase
from .model.hermes import HermesModelGateway, HermesGatewayError, ModelInvocationResult
from .validation import DigestValidationError, validate_digest


class RunnerError(RuntimeError):
    pass


_DURATION_RE = re.compile(r"^(\d+)([mhd])$", re.IGNORECASE)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    workflow_id: str
    status: str
    message_count: int
    cutoff_seq: int
    window_start: str | None
    window_end: str | None
    provider: str | None = None
    model: str | None = None
    reasoning: str | None = None
    digest: dict[str, Any] | None = None


def _parse_last(value: str, *, now: datetime) -> datetime:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise RunnerError("--last must use a duration such as 30m, 24h, or 2d")
    amount = int(match.group(1))
    if amount <= 0:
        raise RunnerError("--last duration must be greater than zero")
    unit = match.group(2).lower()
    delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
    return now - delta


def _parse_since(value: str, *, workflow: WorkflowConfig) -> datetime:
    text = value.strip()
    if not text:
        raise RunnerError("--since must not be empty")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RunnerError("--since must be ISO-8601 or 'YYYY-MM-DD HH:MM'") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(workflow.timezone))
    return parsed.astimezone(timezone.utc)


def _window(rows: list[Any]) -> tuple[str | None, str | None]:
    if not rows:
        return None, None
    timestamps = [str(row["occurred_at"]) for row in rows]
    return min(timestamps), max(timestamps)


def _new_run_id(workflow_id: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{workflow_id}-{uuid4().hex[:8]}"


def run_workflow(
    config: AppConfig,
    workflow_id: str,
    *,
    last: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    gateway: HermesModelGateway | None = None,
) -> RunResult:
    if not dry_run:
        raise RunnerError("delivery-capable runs are not enabled until Phase 5; use --dry-run")
    if last and since:
        raise RunnerError("use only one of --last or --since")

    workflow = config.workflow(workflow_id)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = _new_run_id(workflow.id, current)

    with DigestDatabase(config.database_path) as db:
        state = db.workflow_state(workflow.id)
        initialized = bool(state["initialized"])
        checkpoint = int(state["checkpoint_seq"] or 0)
        cutoff = db.latest_seq()

        explicit_since: datetime | None = None
        if last:
            explicit_since = _parse_last(last, now=current)
        elif since:
            explicit_since = _parse_since(since, workflow=workflow)

        if not initialized and explicit_since is None:
            raise RunnerError(
                f"workflow '{workflow.id}' needs its initial window; use --last or --since"
            )

        if explicit_since is not None:
            rows = db.select_since(workflow.group_jid, explicit_since.isoformat(), cutoff)
        else:
            rows = db.select_changes(workflow.group_jid, checkpoint, cutoff)

        usable = [
            row for row in rows
            if not row["deleted"] and bool((row["text"] or row["caption"] or "").strip())
        ]
        window_start, window_end = _window(usable)

        db.create_run(
            run_id=run_id,
            workflow_id=workflow.id,
            mode="dry-run",
            checkpoint_before=checkpoint if initialized else None,
            cutoff_seq=cutoff,
            message_count=len(usable),
        )

        if not rows:
            db.finish_run(run_id, status="dry-run-empty")
            return RunResult(
                run_id, workflow.id, "no-pending-messages", 0, cutoff,
                None, None, digest={"sections": []},
            )

        if not usable:
            db.finish_run(run_id, status="dry-run-empty")
            return RunResult(
                run_id, workflow.id, "no-usable-messages", 0, cutoff,
                None, None, digest={"sections": []},
            )

        model_gateway = gateway or HermesModelGateway(config)
        try:
            model_result: ModelInvocationResult = model_gateway.summarize(workflow, usable)
        except HermesGatewayError as exc:
            db.finish_run(
                run_id,
                status="failed",
                failure_stage="model",
                failure_reason=str(exc)[:1000],
            )
            raise RunnerError(f"model generation failed: {exc}") from exc

        try:
            digest = validate_digest(model_result.raw_output, workflow, usable)
        except DigestValidationError as exc:
            db.finish_run(
                run_id,
                status="failed",
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
                failure_stage="validation",
                failure_reason=str(exc)[:1000],
            )
            raise RunnerError(f"model output validation failed: {exc}") from exc

        db.finish_run(
            run_id,
            status="dry-run-success",
            provider=model_result.provider,
            model=model_result.model,
            reasoning=model_result.reasoning,
        )

        return RunResult(
            run_id=run_id,
            workflow_id=workflow.id,
            status="dry-run-success" if digest["sections"] else "dry-run-no-material-updates",
            message_count=len(usable),
            cutoff_seq=cutoff,
            window_start=window_start,
            window_end=window_end,
            provider=model_result.provider,
            model=model_result.model,
            reasoning=model_result.reasoning,
            digest=digest,
        )
