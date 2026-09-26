"""Command-line interface for WhatsApp Technical Digest v2."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import ConfigError, load_config
from .database import DigestDatabase


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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config" and args.config_command == "validate":
            return _cmd_validate(args.config)
        if args.command == "status":
            return _cmd_status(args.config)
        if args.command == "whatsapp" and args.whatsapp_command == "groups":
            return _cmd_groups(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
