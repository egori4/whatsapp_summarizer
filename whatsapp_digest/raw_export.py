"""Human-readable export of the exact source records supplied to the model."""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from .renderer import RenderContext


def render_raw_messages(
    records: Sequence[Mapping[str, str]],
    context: RenderContext,
    *,
    context_records: Sequence[Mapping[str, str]] = (),
) -> str:
    lines = [
        f"Workflow: {context.workflow_name}",
        f"Run ID: {context.run_id}",
        f"Current window: {context.window_start or '-'} -> {context.window_end or '-'}",
        f"Messages: {len(records)}",
        f"Current messages: {len(records)}",
        f"Historical context messages: {len(context_records)}",
        f"Context lookback: {context.context_lookback or 'disabled'}",
        "",
        "The records below are the exact sanitized source records supplied to the model.",
        "",
    ]
    if context_records:
        lines.extend(["HISTORICAL CONTEXT — ALREADY PROCESSED", "=" * 40, ""])
        for record in context_records:
            lines.append(f"[{record['timestamp']}] {record['sender']}")
            lines.append(f"Source ID: {record['source_id']}")
            lines.append(record["text"])
            lines.append("")
    lines.extend(["CURRENT MESSAGES — ELIGIBLE FOR THIS DIGEST", "=" * 42, ""])
    for record in records:
        lines.append(f"[{record['timestamp']}] {record['sender']}")
        lines.append(f"Source ID: {record['source_id']}")
        lines.append(record["text"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
