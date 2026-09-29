"""Workflow runner for WhatsApp Technical Digest v2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import re
from string import Formatter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import AppConfig, WorkflowConfig
from .database import DigestDatabase
from .delivery.base import DeliveryChannel, DeliveryError
from .delivery.email import EmailDelivery
from .locking import WorkflowBusyError, workflow_lock
from .model.hermes import HermesModelGateway, HermesGatewayError, ModelInvocationResult, source_record
from .raw_export import render_raw_messages
from .renderer import RenderContext, render_digest
from .validation import DigestValidationError, validate_digest


logger = logging.getLogger(__name__)


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
    rendered_text: str | None = None
    rendered_html: str | None = None
    raw_text: str | None = None


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


def _format_subject(workflow: WorkflowConfig, local_now: datetime, run_id: str) -> str:
    try:
        return workflow.email["subject"].format(
            date=local_now.strftime("%Y-%m-%d"),
            workflow_name=workflow.name,
            workflow_id=workflow.id,
            run_id=run_id,
        )
    except (KeyError, ValueError) as exc:
        raise RunnerError(f"invalid email subject template: {exc}") from exc


def _notify_failure(
    channel: DeliveryChannel,
    workflow: WorkflowConfig,
    *,
    run_id: str,
    stage: str,
    reason: str,
    local_now: datetime,
) -> None:
    try:
        channel.send_failure(
            workflow,
            run_id=run_id,
            stage=stage,
            reason=reason[:1000],
            date=local_now.strftime("%Y-%m-%d"),
        )
    except DeliveryError:
        pass


def _apply_retention(db: DigestDatabase, config: AppConfig) -> None:
    try:
        removed = db.purge_processed_messages(
            {workflow.id: workflow.group_jid for workflow in config.workflows},
            config.processed_raw_days,
        )
    except Exception as exc:
        logger.warning(
            "processed-message retention failed: %s",
            type(exc).__name__,
        )
        return
    if removed:
        logger.info("processed-message retention removed=%d", removed)


def _run_workflow_unlocked(
    config: AppConfig,
    workflow_id: str,
    *,
    last: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    scheduled: bool = False,
    now: datetime | None = None,
    gateway: HermesModelGateway | None = None,
    delivery: DeliveryChannel | None = None,
) -> RunResult:
    if last and since:
        raise RunnerError("use only one of --last or --since")

    workflow = config.workflow(workflow_id)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_now = current.astimezone(ZoneInfo(workflow.timezone))
    run_id = _new_run_id(workflow.id, current)
    mode = "dry-run" if dry_run else ("scheduled" if scheduled else "run")
    channel = delivery or EmailDelivery(config)

    with DigestDatabase(config.database_path) as db:
        state = db.workflow_state(workflow.id)
        initialized = bool(state["initialized"])
        checkpoint = int(state["checkpoint_seq"] or 0)
        cutoff = db.latest_seq()
        logger.info(
            "[%s] run started run_id=%s mode=%s checkpoint=%s cutoff=%s",
            workflow.id,
            run_id,
            mode,
            checkpoint if initialized else "uninitialized",
            cutoff,
        )

        explicit_since: datetime | None = None
        if last:
            explicit_since = _parse_last(last, now=current)
        elif since:
            explicit_since = _parse_since(since, workflow=workflow)

        if not initialized and explicit_since is None:
            if scheduled:
                db.create_run(
                    run_id=run_id,
                    workflow_id=workflow.id,
                    mode=mode,
                    checkpoint_before=None,
                    cutoff_seq=cutoff,
                    message_count=0,
                )
                db.finish_run(run_id, status="needs-initial-run")
                logger.warning(
                    "[%s] scheduled run skipped state=NEEDS_INITIAL_RUN run_id=%s",
                    workflow.id,
                    run_id,
                )
                return RunResult(
                    run_id=run_id,
                    workflow_id=workflow.id,
                    status="needs-initial-run",
                    message_count=0,
                    cutoff_seq=cutoff,
                    window_start=None,
                    window_end=None,
                    digest={"sections": []},
                )
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
        usable.sort(
            key=lambda row: (
                str(row["occurred_at"]),
                int(row["change_seq"]),
                str(row["message_id"]),
            )
        )
        window_start, window_end = _window(usable)
        context_rows: list[Any] = []
        context_lookback: str | None = None
        if initialized and explicit_since is None and config.context["enabled"] and usable:
            context_start = current - timedelta(seconds=int(config.context["lookback_seconds"]))
            context_rows = db.select_context(
                workflow.group_jid,
                checkpoint,
                context_start.isoformat(),
                int(config.context["max_messages"]),
            )
            context_lookback = str(config.context["lookback"])

        logger.info(
            "[%s] selected_messages=%d pending_changes=%d context_messages=%d context_lookback=%s",
            workflow.id,
            len(usable),
            len(rows),
            len(context_rows),
            context_lookback or "disabled",
        )

        db.create_run(
            run_id=run_id,
            workflow_id=workflow.id,
            mode=mode,
            checkpoint_before=checkpoint if initialized else None,
            cutoff_seq=cutoff,
            message_count=len(usable),
        )

        if not rows:
            if dry_run:
                db.finish_run(run_id, status="dry-run-empty")
                status = "no-pending-messages"
            else:
                db.complete_success_and_checkpoint(
                    run_id,
                    workflow.id,
                    cutoff,
                    status="success-no-pending",
                )
                _apply_retention(db, config)
                status = "success-no-pending"
            return RunResult(
                run_id, workflow.id, status, 0, cutoff,
                None, None, digest={"sections": []},
            )

        if not usable:
            if dry_run:
                db.finish_run(run_id, status="dry-run-empty")
                status = "no-usable-messages"
            else:
                db.complete_success_and_checkpoint(
                    run_id,
                    workflow.id,
                    cutoff,
                    status="success-no-usable",
                )
                _apply_retention(db, config)
                status = "success-no-usable"
            return RunResult(
                run_id, workflow.id, status, 0, cutoff,
                None, None, digest={"sections": []},
            )

        model_gateway = gateway or HermesModelGateway(config)
        try:
            model_result: ModelInvocationResult = model_gateway.summarize(
                workflow, usable, context_rows=context_rows
            )
        except HermesGatewayError as exc:
            reason = str(exc)
            db.finish_run(
                run_id,
                status="failed",
                failure_stage="model",
                failure_reason=reason[:1000],
            )
            if not dry_run:
                _notify_failure(
                    channel, workflow, run_id=run_id, stage="model",
                    reason=reason, local_now=local_now,
                )
            raise RunnerError(f"model generation failed: {exc}") from exc

        logger.info(
            "[%s] provider=%s model=%s reasoning=%s",
            workflow.id,
            model_result.provider,
            model_result.model,
            model_result.reasoning,
        )

        if model_result.context_source_records is None:
            used_context_rows = context_rows
            used_context_records = tuple(source_record(row) for row in context_rows)
        else:
            used_context_records = model_result.context_source_records
            used_context_ids = {record["source_id"] for record in used_context_records}
            used_context_rows = [row for row in context_rows if str(row["message_id"]) in used_context_ids]
        if len(used_context_rows) != len(context_rows):
            logger.info(
                "[%s] context trimmed candidates=%d used=%d",
                workflow.id, len(context_rows), len(used_context_rows),
            )

        try:
            digest = validate_digest(
                model_result.raw_output, workflow, usable, context_rows=used_context_rows
            )
        except DigestValidationError as exc:
            reason = str(exc)

            if os.environ.get("WHATSAPP_DIGEST_DEBUG_MODEL_OUTPUT") == "1":
                try:
                    debug_dir = config.database_path.parent / "debug-model-output"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    debug_path = debug_dir / f"{run_id}.txt"
                    fd = os.open(debug_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(model_result.raw_output)
                    logger.warning("[%s] raw model output saved to %s", workflow.id, debug_path)
                except OSError as debug_exc:
                    logger.warning(
                        "[%s] failed to save raw model output for run_id=%s: %s",
                        workflow.id, run_id, debug_exc,
                    )

            db.finish_run(
                run_id,
                status="failed",
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
                failure_stage="validation",
                failure_reason=reason[:1000],
            )
            if not dry_run:
                _notify_failure(
                    channel, workflow, run_id=run_id, stage="validation",
                    reason=reason, local_now=local_now,
                )
            raise RunnerError(f"model output validation failed: {exc}") from exc

        logger.info("[%s] validation passed run_id=%s", workflow.id, run_id)

        render_context = RenderContext(
            workflow_name=workflow.name,
            workflow_id=workflow.id,
            run_id=run_id,
            generated_at=local_now,
            timezone=workflow.timezone,
            provider=model_result.provider,
            model=model_result.model,
            reasoning=model_result.reasoning,
            message_count=len(usable),
            window_start=window_start,
            window_end=window_end,
            context_count=len(used_context_rows),
            context_lookback=context_lookback,
        )
        try:
            rendered = render_digest(digest, render_context)
            model_records = model_result.source_records or tuple(source_record(row) for row in usable)
            raw_text = render_raw_messages(
                model_records, render_context, context_records=used_context_records
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            db.finish_run(
                run_id,
                status="failed",
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
                failure_stage="render",
                failure_reason=reason[:1000],
            )
            if not dry_run:
                _notify_failure(
                    channel, workflow, run_id=run_id, stage="render",
                    reason=reason, local_now=local_now,
                )
            raise RunnerError(f"rendering failed: {exc}") from exc

        if dry_run:
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
                rendered_text=rendered.text,
                rendered_html=rendered.html,
                raw_text=raw_text,
            )

        if not db.sources_are_fresh([*usable, *used_context_rows]):
            reason = "one or more selected source messages changed after the run snapshot"
            db.finish_run(
                run_id,
                status="failed",
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
                failure_stage="freshness",
                failure_reason=reason,
            )
            _notify_failure(
                channel, workflow, run_id=run_id, stage="freshness",
                reason=reason, local_now=local_now,
            )
            raise RunnerError("source messages changed during the run; checkpoint was not advanced")

        if not digest["sections"]:
            db.complete_success_and_checkpoint(
                run_id,
                workflow.id,
                cutoff,
                status="success-no-material",
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
            )
            _apply_retention(db, config)
            return RunResult(
                run_id=run_id,
                workflow_id=workflow.id,
                status="success-no-material",
                message_count=len(usable),
                cutoff_seq=cutoff,
                window_start=window_start,
                window_end=window_end,
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
                digest=digest,
                rendered_text=rendered.text,
                rendered_html=rendered.html,
                raw_text=raw_text,
            )

        subject = _format_subject(workflow, local_now, run_id)
        try:
            channel.send_digest(
                workflow,
                subject=subject,
                text=rendered.text,
                html=rendered.html,
                raw_text=raw_text,
                run_id=run_id,
            )
        except DeliveryError as exc:
            db.finish_run(
                run_id,
                status="failed",
                provider=model_result.provider,
                model=model_result.model,
                reasoning=model_result.reasoning,
                failure_stage="delivery",
                failure_reason=str(exc)[:1000],
            )
            raise RunnerError(f"email delivery failed: {exc}") from exc

        db.complete_success_and_checkpoint(
            run_id,
            workflow.id,
            cutoff,
            status="delivered",
            provider=model_result.provider,
            model=model_result.model,
            reasoning=model_result.reasoning,
        )
        logger.info(
            "[%s] checkpoint advanced run_id=%s checkpoint=%s status=delivered",
            workflow.id,
            run_id,
            cutoff,
        )
        _apply_retention(db, config)
        return RunResult(
            run_id=run_id,
            workflow_id=workflow.id,
            status="delivered",
            message_count=len(usable),
            cutoff_seq=cutoff,
            window_start=window_start,
            window_end=window_end,
            provider=model_result.provider,
            model=model_result.model,
            reasoning=model_result.reasoning,
            digest=digest,
            rendered_text=rendered.text,
            rendered_html=rendered.html,
            raw_text=raw_text,
        )



def run_workflow(
    config: AppConfig,
    workflow_id: str,
    *,
    last: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    scheduled: bool = False,
    now: datetime | None = None,
    gateway: HermesModelGateway | None = None,
    delivery: DeliveryChannel | None = None,
) -> RunResult:
    workflow = config.workflow(workflow_id)
    try:
        with workflow_lock(config.database_path, workflow.id):
            return _run_workflow_unlocked(
                config,
                workflow_id,
                last=last,
                since=since,
                dry_run=dry_run,
                scheduled=scheduled,
                now=now,
                gateway=gateway,
                delivery=delivery,
            )
    except WorkflowBusyError as exc:
        logger.warning("[%s] duplicate concurrent run blocked", workflow.id)
        raise RunnerError(str(exc)) from exc
