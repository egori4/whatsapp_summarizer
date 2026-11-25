"""Resubmit a retained canonical daily digest without AI or ledger changes."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

from .config import AppConfig
from .delivery.email import EmailDelivery
from .model.hermes import source_record
from .raw_export import render_raw_messages
from .renderer import RenderContext, render_digest
from .runner import RunnerError, _format_subject


@dataclass(frozen=True)
class ResendResult:
    subject: str
    recipients: tuple[str, ...]
    source_count: int
    context_count: int
    raw_included: bool
    status: str


def resend_daily(config: AppConfig, run_id: str, *, dry_run: bool = False,
                 all_recipients: bool = False, without_raw: bool = False,
                 delivery=None) -> ResendResult:
    # Read-only snapshot; no checkpoint, retention, run or weekly ledger writes.
    conn = sqlite3.connect(config.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        row = conn.execute(
            """SELECT a.*, r.provider, r.model, r.reasoning, r.message_count
               FROM daily_digest_artifacts a JOIN runs r ON r.run_id=a.run_id
               WHERE a.run_id=?""", (run_id,)
        ).fetchone()
        if row is None:
            raise RunnerError("No retained canonical daily digest for this run ID")
        digest = json.loads(row["digest_json"])
        if row["outcome"] not in {"delivered", "smtp-accepted"} or not digest.get("sections"):
            raise RunnerError("This run has no previously submitted material digest")
        workflow = config.workflow(row["workflow_id"])
        if not all_recipients:
            workflow = replace(workflow, email={**workflow.email, "cc": [], "bcc": []})
        original_time = datetime.strptime(run_id.split("-")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        local_time = original_time.astimezone(ZoneInfo(workflow.timezone))
        include_raw = bool(workflow.email["attach_raw_messages"] and not without_raw)
        current = []
        context = []
        if include_raw:
            current = conn.execute(
                """SELECT * FROM messages WHERE group_jid=? AND change_seq>? AND change_seq<=?
                   AND deleted=0 AND TRIM(COALESCE(NULLIF(text,''),caption,''))<>''
                   ORDER BY occurred_at,change_seq,message_id""",
                (workflow.group_jid,row["checkpoint_before"],row["checkpoint_after"])
            ).fetchall()
            if len(current) != row["message_count"]:
                raise RunnerError("Original source window is incomplete; use --without-raw to resend the saved summary only")
            if config.context["enabled"]:
                since = original_time - timedelta(seconds=config.context["lookback_seconds"])
                context = list(reversed(conn.execute(
                    """SELECT * FROM messages WHERE group_jid=? AND change_seq<=? AND occurred_at>=?
                       AND deleted=0 AND TRIM(COALESCE(NULLIF(text,''),caption,''))<>''
                       ORDER BY occurred_at DESC,change_seq DESC,message_id DESC LIMIT ?""",
                    (workflow.group_jid,row["checkpoint_before"],since.isoformat(),config.context["max_messages"])
                ).fetchall()))
        rc = RenderContext(
            workflow.name,workflow.id,run_id,local_time,workflow.timezone,
            row["provider"] or "unknown",row["model"] or "unknown",row["reasoning"] or "unknown",
            row["message_count"],row["window_start"],row["window_end"],
            len(context),config.context["lookback"] if include_raw and config.context["enabled"] else None,
        )
        rendered = render_digest(digest,rc)
        raw = None
        if include_raw:
            raw = render_raw_messages([source_record(r) for r in current],rc,
                                      context_records=[source_record(r) for r in context])
            raw = raw.replace(
                "The records below are the exact sanitized source records supplied to the model.",
                "Reconstructed from retained WhatsApp rows for resend; historical context and model-side truncation may differ from the original attachment.",
            )
        subject = _format_subject(workflow,local_time,run_id)
        recipients = tuple(workflow.email["to"] + workflow.email["cc"] + workflow.email["bcc"])
    finally:
        conn.close()
    if not dry_run:
        (delivery or EmailDelivery(config)).send_digest(
            workflow,subject=subject,text=rendered.text,html=rendered.html,raw_text=raw,run_id=run_id,
        )
    return ResendResult(subject,recipients,row["message_count"],len(context),include_raw,
                        "dry-run" if dry_run else "smtp-accepted")
