"""Command-line interface for WhatsApp Technical Digest v2."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .config import ConfigError, load_config
from .database import DigestDatabase
from .collector import WhatsAppCollector
from .runner import RunnerError, RunResult, run_workflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digest")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate")

    sub.add_parser("status")

    whatsapp = sub.add_parser("whatsapp")
    whatsapp_sub = whatsapp.add_subparsers(dest="whatsapp_command", required=True)
    whatsapp_sub.add_parser("groups")

    collector = sub.add_parser("collector")
    collector_sub = collector.add_subparsers(dest="collector_command", required=True)
    collector_sub.add_parser("once")
    collector_sub.add_parser("run")

    run = sub.add_parser("run")
    run.add_argument("workflow", help="Workflow ID")
    window = run.add_mutually_exclusive_group()
    window.add_argument("--last", help="Initial/manual window such as 24h or 2d")
    window.add_argument("--since", help="Initial/manual start time (ISO-8601 or YYYY-MM-DD HH:MM)")
    run.add_argument("--dry-run", action="store_true", help="Generate and validate without email or checkpoint update")

    return parser


def _cmd_validate(path: str) -> int:
    cfg = load_config(path)
    print(f"Configuration valid: {cfg.source_path}")
    print(f"Workflows: {len(cfg.workflows)}")
    return 0


def _cmd_status(path: str) -> int:
    cfg = load_config(path)
    with DigestDatabase(cfg.database_path) as db:
        for workflow in cfg.workflows:
            state = db.workflow_state(workflow.id)
            pending = db.pending_count(workflow.id, workflow.group_jid)
            if state["initialized"]:
                status = "READY"
            else:
                status = "NEEDS_INITIAL_RUN"
            print(workflow.name)
            print(f"  ID: {workflow.id}")
            print(f"  State: {status}")
            print(f"  Pending changes: {pending}")
            if state["last_success_at"]:
                print(f"  Last success: {state['last_success_at']}")
            if workflow.schedule:
                schedule = workflow.schedule
                kind = schedule["type"]
                if kind == "daily":
                    detail = f"daily {schedule['at']}"
                elif kind == "weekly":
                    detail = f"weekly {','.join(schedule['days'])} {schedule['at']}"
                else:
                    detail = f"monthly day {schedule['day']} {schedule['at']}"
                print(f"  Schedule: {detail} {schedule['timezone']}")
            else:
                print("  Schedule: manual only")
    return 0


def _cmd_groups(path: str) -> int:
    cfg = load_config(path)
    configured = {workflow.group_jid: workflow.id for workflow in cfg.workflows}
    with DigestDatabase(cfg.database_path) as db:
        rows = db.list_discovered_groups()
    if not rows:
        print("No WhatsApp groups discovered yet.")
        return 0
    for row in rows:
        label = row["display_name"] or "(name unavailable)"
        workflow_id = configured.get(row["group_jid"])
        suffix = f" [configured: {workflow_id}]" if workflow_id else ""
        print(f"{label}")
        print(f"  JID: {row['group_jid']}{suffix}")
        print(f"  Last seen: {row['last_seen']}")
    return 0


def _cmd_collector(path: str, *, once: bool) -> int:
    cfg = load_config(path)
    with DigestDatabase(cfg.database_path) as db:
        collector = WhatsAppCollector(cfg, db)
        if once:
            stats = collector.collect_once()
            print(
                f"received={stats.received} stored={stats.stored} "
                f"updated={stats.updated} discovered_groups={stats.discovered_groups} "
                f"ignored_direct={stats.ignored_direct}"
            )
            return 0
        collector.run_forever()
    return 0


def _write_private_text(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def _save_dry_run_artifacts(database_path: Path, result: RunResult) -> Path | None:
    if result.rendered_text is None or result.rendered_html is None or result.raw_text is None:
        return None
    root = database_path.parent / "dry-runs" / result.run_id
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    _write_private_text(root / "digest.txt", result.rendered_text)
    _write_private_text(root / "digest.html", result.rendered_html)
    _write_private_text(root / "raw_messages.txt", result.raw_text)
    return root


def _cmd_run(path: str, *, workflow: str, last: str | None, since: str | None, dry_run: bool) -> int:
    cfg = load_config(path)
    result = run_workflow(
        cfg,
        workflow,
        last=last,
        since=since,
        dry_run=dry_run,
    )
    if dry_run and result.rendered_text:
        print(result.rendered_text.rstrip())
        artifact_dir = _save_dry_run_artifacts(cfg.database_path, result)
        if artifact_dir:
            print()
            print(f"Dry-run artifacts: {artifact_dir}")
            print(f"  Text: {artifact_dir / 'digest.txt'}")
            print(f"  HTML: {artifact_dir / 'digest.html'}")
            print(f"  Raw:  {artifact_dir / 'raw_messages.txt'}")
        return 0

    print(f"Run ID: {result.run_id}")
    print(f"Workflow: {result.workflow_id}")
    print(f"Status: {result.status}")
    print(f"Messages: {result.message_count}")
    if result.provider:
        print(f"Provider: {result.provider}")
    if result.model:
        print(f"Model: {result.model}")
    if result.reasoning:
        print(f"Reasoning: {result.reasoning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config" and args.config_command == "validate":
            return _cmd_validate(args.config)
        if args.command == "status":
            return _cmd_status(args.config)
        if args.command == "whatsapp" and args.whatsapp_command == "groups":
            return _cmd_groups(args.config)
        if args.command == "collector":
            return _cmd_collector(args.config, once=args.collector_command == "once")
        if args.command == "run":
            return _cmd_run(
                args.config,
                workflow=args.workflow,
                last=args.last,
                since=args.since,
                dry_run=args.dry_run,
            )
    except (ConfigError, RunnerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
