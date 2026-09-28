"""systemd user-timer generation and lifecycle for scheduled workflows."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from .config import AppConfig, WorkflowConfig


class ScheduleError(RuntimeError):
    pass


_WEEKDAY = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}


@dataclass(frozen=True)
class UnitFiles:
    workflow_id: str
    base_name: str
    service_name: str
    timer_name: str
    service_text: str
    timer_text: str
    on_calendar: str


def systemd_user_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "systemd" / "user").resolve()


def schedule_env_file(config: AppConfig) -> Path:
    return config.source_path.parent / "digest.env"


def on_calendar(schedule: dict[str, Any]) -> str:
    kind = schedule["type"]
    at = f"{schedule['at']}:00"
    timezone = schedule["timezone"]
    if kind == "daily":
        return f"*-*-* {at} {timezone}"
    if kind == "weekly":
        days = ",".join(_WEEKDAY[day] for day in schedule["days"])
        return f"{days} *-*-* {at} {timezone}"
    if kind == "monthly":
        return f"*-*-{int(schedule['day']):02d} {at} {timezone}"
    raise ScheduleError(f"unsupported schedule type: {kind}")


def _quote_exec(value: str | Path) -> str:
    text = str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _escape_unit_path(value: str | Path) -> str:
    text = str(value).replace("%", "%%")
    return text.replace("\\", "\\x5c").replace(" ", "\\x20")


def _escape_environment_file(value: str | Path) -> str:
    return _escape_unit_path(value)


def render_units(
    config: AppConfig,
    workflow: WorkflowConfig,
    *,
    python_executable: str | Path | None = None,
) -> UnitFiles:
    if workflow.schedule is None:
        raise ScheduleError(f"workflow '{workflow.id}' is manual-only")

    python = Path(python_executable or sys.executable).resolve()
    base_name = f"whatsapp-digest-{workflow.id}"
    service_name = f"{base_name}.service"
    timer_name = f"{base_name}.timer"
    calendar = on_calendar(workflow.schedule)
    env_file = schedule_env_file(config)

    service = "\n".join(
        [
            "[Unit]",
            f"Description=WhatsApp Technical Digest - {workflow.name}",
            "Wants=network-online.target",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={_escape_unit_path(config.source_path.parent)}",
            f"EnvironmentFile=-{_escape_environment_file(env_file)}",
            (
                "ExecStart="
                f"{_quote_exec(python)} -m whatsapp_digest.cli "
                f"--config {_quote_exec(config.source_path)} "
                f"run {_quote_exec(workflow.id)} --scheduled"
            ),
            "",
        ]
    )
    timer = "\n".join(
        [
            "[Unit]",
            f"Description=Schedule WhatsApp Technical Digest - {workflow.name}",
            "",
            "[Timer]",
            f"OnCalendar={calendar}",
            "Persistent=true",
            "AccuracySec=1min",
            f"Unit={service_name}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    return UnitFiles(
        workflow_id=workflow.id,
        base_name=base_name,
        service_name=service_name,
        timer_name=timer_name,
        service_text=service,
        timer_text=timer,
        on_calendar=calendar,
    )


def _run_systemctl(
    args: list[str],
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = executor(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ScheduleError(f"failed to execute systemctl: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl failed").strip()
        raise ScheduleError(detail)
    return result


def _write_unit(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o644)


def _verify_units(
    paths: list[Path],
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    if not paths:
        return
    try:
        result = executor(
            ["systemd-analyze", "--user", "verify", *[str(path) for path in paths]],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ScheduleError(f"failed to execute systemd-analyze: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemd unit verification failed").strip()
        raise ScheduleError(detail)


def install_schedules(
    config: AppConfig,
    *,
    unit_dir: str | Path | None = None,
    python_executable: str | Path | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[UnitFiles]:
    root = Path(unit_dir or systemd_user_dir())
    root.mkdir(parents=True, exist_ok=True)

    desired: dict[str, UnitFiles] = {}
    for workflow in config.workflows:
        if workflow.schedule is None:
            continue
        unit = render_units(config, workflow, python_executable=python_executable)
        desired[unit.base_name] = unit
        _write_unit(root / unit.service_name, unit.service_text)
        _write_unit(root / unit.timer_name, unit.timer_text)

    existing = {
        path.stem
        for path in root.glob("whatsapp-digest-*.timer")
        if path.is_file()
    }
    stale = sorted(existing - set(desired))
    for base_name in stale:
        _run_systemctl(
            ["disable", "--now", f"{base_name}.timer"],
            executor=executor,
            check=False,
        )
        for suffix in (".timer", ".service"):
            path = root / f"{base_name}{suffix}"
            if path.exists():
                path.unlink()

    unit_paths: list[Path] = []
    for unit in desired.values():
        unit_paths.extend([root / unit.service_name, root / unit.timer_name])
    _verify_units(unit_paths, executor=executor)

    _run_systemctl(["daemon-reload"], executor=executor)
    for unit in desired.values():
        _run_systemctl(["enable", "--now", unit.timer_name], executor=executor)
        active = _run_systemctl(
            ["is-active", unit.timer_name],
            executor=executor,
            check=False,
        )
        if active.returncode != 0 or (active.stdout or "").strip() != "active":
            detail = (active.stderr or active.stdout or "timer did not become active").strip()
            raise ScheduleError(f"{unit.timer_name} is not active after install: {detail}")
    return list(desired.values())


def _unit_state(
    unit_name: str,
    verb: str,
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    result = _run_systemctl([verb, unit_name], executor=executor, check=False)
    text = (result.stdout or result.stderr or "").strip().splitlines()
    if text:
        return text[-1]
    return "unknown" if result.returncode else "active"


def schedule_status(
    config: AppConfig,
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for workflow in config.workflows:
        if workflow.schedule is None:
            result.append({
                "workflow_id": workflow.id,
                "schedule": "manual-only",
                "enabled": "-",
                "active": "-",
                "next": "-",
                "health": "manual-only",
            })
            continue
        unit = render_units(config, workflow)
        enabled = _unit_state(unit.timer_name, "is-enabled", executor=executor)
        active = _unit_state(unit.timer_name, "is-active", executor=executor)
        next_result = _run_systemctl(
            ["show", unit.timer_name, "--property=NextElapseUSecRealtime", "--value"],
            executor=executor,
            check=False,
        )
        next_value = (next_result.stdout or "").strip() or "-"
        result.append({
            "workflow_id": workflow.id,
            "schedule": unit.on_calendar,
            "enabled": enabled,
            "active": active,
            "next": next_value,
            "health": "healthy" if enabled == "enabled" and active == "active" and next_value != "-" else "unhealthy",
        })
    return result


def remove_schedules(
    config: AppConfig,
    *,
    unit_dir: str | Path | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    root = Path(unit_dir or systemd_user_dir())
    removed: list[str] = []
    if not root.exists():
        return removed

    bases = sorted({
        path.stem
        for path in root.glob("whatsapp-digest-*.timer")
        if path.is_file()
    })
    for base_name in bases:
        _run_systemctl(
            ["disable", "--now", f"{base_name}.timer"],
            executor=executor,
            check=False,
        )
        for suffix in (".timer", ".service"):
            path = root / f"{base_name}{suffix}"
            if path.exists():
                path.unlink()
        removed.append(base_name)

    _run_systemctl(["daemon-reload"], executor=executor)
    return removed
