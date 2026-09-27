"""Human-readable export of the exact source records supplied to the model."""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from .renderer import RenderContext


def render_raw_messages(
    records: Sequence[Mapping[str, str]],
    context: RenderContext,
) -> str:
    lines = [
        f"Workflow: {context.workflow_name}",
        f"Run ID: {context.run_id}",
        f"Window: {context.window_start or '-'} -> {context.window_end or '-'}",
        f"Messages: {len(records)}",
        "",
        "The records below are the exact sanitized source window supplied to the model.",
        "",
    ]
    for record in records:
        lines.append(f"[{record['timestamp']}] {record['sender']}")
        lines.append(f"Source ID: {record['source_id']}")
        lines.append(record["text"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
