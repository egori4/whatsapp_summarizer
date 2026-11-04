"""Deterministic text and HTML rendering for validated digests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any


@dataclass(frozen=True)
class RenderContext:
    workflow_name: str
    workflow_id: str
    run_id: str
    generated_at: datetime
    timezone: str
    provider: str
    model: str
    reasoning: str
    message_count: int
    window_start: str | None
    window_end: str | None
    context_count: int = 0
    context_lookback: str | None = None
    weekly_period: str | None = None


@dataclass(frozen=True)
class RenderedDigest:
    text: str
    html: str


def _metadata_lines(context: RenderContext) -> list[str]:
    local = context.generated_at
    generated = local.strftime("%Y-%m-%d %H:%M:%S")
    window = f"{context.window_start or '-'} -> {context.window_end or '-'}"
    return [
        f"Workflow: {context.workflow_name}",
        *([f"Weekly period: {context.weekly_period}"] if context.weekly_period else []),
        f"Generated: {generated} {context.timezone}",
        f"Provider: {context.provider}",
        f"Model: {context.model}",
        f"Reasoning: {context.reasoning}",
        f"{'Daily evidence items' if context.weekly_period else 'Messages processed'}: {context.message_count}",
        f"Context: {context.context_count} prior messages ({context.context_lookback} lookback)" if context.context_lookback else "Context: disabled",
        f"Window: {window}",
        f"Run ID: {context.run_id}",
    ]


def _append_text_list(lines: list[str], label: str, values: list[str]) -> None:
    if not values:
        return
    lines.append(f"{label}:")
    for value in values:
        lines.append(f"  - {value}")


def render_text(digest: dict[str, Any], context: RenderContext) -> str:
    heading = (f"{context.workflow_name} — Weekly Review ({context.weekly_period})"
               if context.weekly_period else f"{context.workflow_name} — {context.generated_at:%Y-%m-%d}")
    lines = [heading]
    sections = digest.get("sections") or []
    if not sections:
        lines.extend(["", "No material updates."])
        lines.extend(["", "---", *_metadata_lines(context)])
        return "\n".join(lines).rstrip() + "\n"

    for section in sections:
        items = section.get("items") or []
        if not items:
            continue
        lines.extend(["", section["title"]])
        lines.append("=" * len(section["title"]))
        for item in items:
            lines.extend(["", item["title"]])
            lines.append(item["summary"])
            _append_text_list(lines, "Details", item.get("details") or [])
            _append_text_list(lines, "Actions", item.get("actions") or [])
            _append_text_list(lines, "Questions", item.get("questions") or [])
            names = list(dict.fromkeys((item.get("reported_by") or []) + (item.get("contributors") or [])))
            if names:
                lines.append("Contacts: " + ", ".join(names))
            _append_text_list(lines, "References", item.get("references") or [])
            _append_text_list(lines, "Daily digest dates", item.get("evidence_dates") or [])
    lines.extend(["", "---", *_metadata_lines(context)])
    return "\n".join(lines).rstrip() + "\n"


def _html_list(label: str, values: list[str]) -> str:
    if not values:
        return ""
    items = "".join(f"<li>{escape(value)}</li>" for value in values)
    return f"<div><strong>{escape(label)}:</strong><ul>{items}</ul></div>"


def render_html(digest: dict[str, Any], context: RenderContext) -> str:
    metadata = "".join(
        f"<tr><th>{escape(line.split(':', 1)[0])}</th><td>{escape(line.split(':', 1)[1].strip())}</td></tr>"
        for line in _metadata_lines(context)
    )
    section_html: list[str] = []
    sections = digest.get("sections") or []
    for section in sections:
        items = section.get("items") or []
        if not items:
            continue
        item_html: list[str] = []
        for item in items:
            extras = [
                _html_list("Details", item.get("details") or []),
                _html_list("Actions", item.get("actions") or []),
                _html_list("Questions", item.get("questions") or []),
            ]
            names = list(dict.fromkeys((item.get("reported_by") or []) + (item.get("contributors") or [])))
            if names:
                extras.append(f'<div class="contacts"><strong>Contacts:</strong> {escape(", ".join(names))}</div>')
            extras.append(_html_list("References", item.get("references") or []))
            extras.append(_html_list("Daily digest dates", item.get("evidence_dates") or []))
            item_html.append(
                "<article>"
                f"<h3>{escape(item['title'])}</h3>"
                f"<p>{escape(item['summary'])}</p>"
                + "".join(extras)
                + "</article>"
            )
        section_html.append(
            f"<section><h2>{escape(section['title'])}</h2>{''.join(item_html)}</section>"
        )

    body = "".join(section_html) if section_html else "<p><strong>No material updates.</strong></p>"
    heading = (f"{context.workflow_name} — Weekly Review ({context.weekly_period})"
               if context.weekly_period else f"{context.workflow_name} — {context.generated_at:%Y-%m-%d}")
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>WhatsApp Digest</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;line-height:1.45;max-width:900px;margin:24px auto;padding:0 16px;color:#222}"
        "table{border-collapse:collapse;margin-bottom:24px}th,td{text-align:left;vertical-align:top;padding:3px 12px 3px 0}"
        "h2{margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:5px}h3{margin-bottom:5px}"
        "article{margin-bottom:18px}ul{margin-top:4px}.contacts{font-size:13px;color:#555;margin-top:6px}.metadata{margin-top:24px;color:#666;font-size:12px}"
        "</style></head><body>"
        f"<h2>{escape(heading)}</h2>{body}<div class=\"metadata\"><table>{metadata}</table></div>"
        "</body></html>\n"
    )


def render_digest(digest: dict[str, Any], context: RenderContext) -> RenderedDigest:
    return RenderedDigest(
        text=render_text(digest, context),
        html=render_html(digest, context),
    )
