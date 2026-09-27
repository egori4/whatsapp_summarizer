"""Command-line interface for WhatsApp Technical Digest v2."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .config import ConfigError, load_config
from .database import DigestDatabase
from .collector import WhatsAppCollector
from .delivery.base import DeliveryError
from .delivery.email import EmailDelivery
from .logging_setup import setup_logging
from .runner import RunnerError, RunResult, run_workflow
from .systemd import (
    ScheduleError,
    install_schedules,
    remove_schedules,
    schedule_env_file,
    schedule_status,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digest")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("validate")

    status = sub.add_parser("status")
    status.add_argument("workflow", nargs="?", help="Optional workflow ID")

    whatsapp = sub.add_parser("whatsapp")
    whatsapp_sub = whatsapp.add_subparsers(dest="whatsapp_command", required=True)
    whatsapp_sub.add_parser("groups")

    collector = sub.add_parser("collector")
    collector_sub = collector.add_subparsers(dest="collector_command", required=True)
    collector_sub.add_parser("once")
    collector_sub.add_parser("run")

    email = sub.add_parser("email")
    email_sub = email.add_subparsers(dest="email_command", required=True)
    email_test = email_sub.add_parser("test")
    email_test.add_argument("workflow", help="Workflow ID")

    run = sub.add_parser("run")
    run.add_argument("workflow", help="Workflow ID")
    window = run.add_mutually_exclusive_group()
    window.add_argument("--last", help="Initial/manual window such as 24h or 2d")
    window.add_argument("--since", help="Initial/manual start time (ISO-8601 or YYYY-MM-DD HH:MM)")
    run.add_argument("--dry-run", action="store_true", help="Generate and validate without email or checkpoint update")
    run.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)

    schedule = sub.add_parser("schedule")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_sub.add_parser("install")
    schedule_sub.add_parser("status")
    schedule_sub.add_parser("remove")

    return parser


def _cmd_validate(path: str) -> int:
    cfg = load_config(path)
    print(f"Configuration valid: {cfg.source_path}")
    print(f"Workflows: {len(cfg.workflows)}")
    return 0


def _cmd_status(path: str, workflow_id: str | None = None) -> int:
    cfg = load_config(path)
    workflows = (cfg.workflow(workflow_id),) if workflow_id else cfg.workflows
    with DigestDatabase(cfg.database_path) as db:
        for workflow in workflows:
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


def _cmd_email_test(path: str, workflow_id: str) -> int:
    cfg = load_config(path)
    workflow = cfg.workflow(workflow_id)
    print(f"SMTP host: {cfg.email['host']}:{cfg.email['port']}")
    print(f"STARTTLS: {cfg.email['starttls']}")
    print(f"Sender: {cfg.email['sender']}")
    print(f"Username: {cfg.email['username'] or '(none)'}")
    print(f"Password env: {cfg.email['password_env']} ({'set' if os.environ.get(cfg.email['password_env']) else 'NOT SET'})")
    print(f"To: {', '.join(workflow.email['to'])}")
    print(f"Cc: {', '.join(workflow.email['cc']) if workflow.email['cc'] else '(none)'}")
    run_id = EmailDelivery(cfg).send_test(workflow)
    print(f"SMTP accepted test message. Run ID: {run_id}")
    print(f"Log: {cfg.log_path}")
    return 0


def _cmd_schedule(path: str, command: str) -> int:
    cfg = load_config(path)
    if command == "install":
        env_file = schedule_env_file(cfg)
        if cfg.email.get("username"):
            if not env_file.is_file():
                raise ScheduleError(
                    f"scheduled SMTP authentication requires {env_file}; "
                    f"create it with {cfg.email['password_env']}=... and chmod 600"
                )
            if env_file.stat().st_mode & 0o077:
                raise ScheduleError(f"{env_file} must not be accessible by group/others; run chmod 600")
        units = install_schedules(cfg)
        if not units:
            print("No scheduled workflows configured; nothing installed.")
            return 0
        for unit in units:
            print(f"{unit.workflow_id}: {unit.timer_name}")
            print(f"  OnCalendar: {unit.on_calendar}")
        print(f"SMTP environment file: {env_file}")
        return 0

    if command == "status":
        for item in schedule_status(cfg):
            print(item["workflow_id"])
            print(f"  Schedule: {item['schedule']}")
            print(f"  Enabled: {item['enabled']}")
            print(f"  Active: {item['active']}")
        return 0

    removed = remove_schedules(cfg)
    if removed:
        for name in removed:
            print(f"Removed: {name}")
    else:
        print("No generated digest schedules found.")
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


def _cmd_run(
    path: str,
    *,
    workflow: str,
    last: str | None,
    since: str | None,
    dry_run: bool,
    scheduled: bool,
) -> int:
    cfg = load_config(path)
    result = run_workflow(
        cfg,
        workflow,
        last=last,
        since=since,
        dry_run=dry_run,
        scheduled=scheduled,
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
    print(f"Log: {cfg.log_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg_for_logging = load_config(args.config)
        setup_logging(cfg_for_logging.log_path, cfg_for_logging.log_days)
    except ConfigError:
        pass

    try:
        if args.command == "config" and args.config_command == "validate":
            return _cmd_validate(args.config)
        if args.command == "status":
            return _cmd_status(args.config, args.workflow)
        if args.command == "whatsapp" and args.whatsapp_command == "groups":
            return _cmd_groups(args.config)
        if args.command == "collector":
            return _cmd_collector(args.config, once=args.collector_command == "once")
        if args.command == "email" and args.email_command == "test":
            return _cmd_email_test(args.config, args.workflow)
        if args.command == "schedule":
            return _cmd_schedule(args.config, args.schedule_command)
        if args.command == "run":
            return _cmd_run(
                args.config,
                workflow=args.workflow,
                last=args.last,
                since=args.since,
                dry_run=args.dry_run,
                scheduled=args.scheduled,
            )
    except (ConfigError, RunnerError, DeliveryError, ScheduleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
