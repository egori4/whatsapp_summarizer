"""Supervision for the dedicated v2 WhatsApp bridge and collector."""
from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Callable
from urllib.request import urlopen

from .config import AppConfig
from .systemd import (
    ScheduleError,
    _escape_unit_path,
    _quote_exec,
    _run_systemctl,
    _unit_state,
    _verify_units,
    _write_unit,
    systemd_user_dir,
)


BRIDGE_SERVICE = "whatsapp-digest-bridge.service"
COLLECTOR_SERVICE = "whatsapp-digest-collector.service"


class TransportError(ScheduleError):
    pass


@dataclass(frozen=True)
class TransportPaths:
    hermes_home: Path
    node: Path
    bridge_script: Path
    session_dir: Path


@dataclass(frozen=True)
class TransportUnits:
    bridge_text: str
    collector_text: str
    paths: TransportPaths
    allowed_groups: tuple[str, ...]


def _hermes_home(value: str | Path | None = None) -> Path:
    if value is not None:
        return Path(value).expanduser().absolute()
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser().absolute() if env else (Path.home() / ".hermes").absolute()


def discover_transport_paths(
    *,
    hermes_home: str | Path | None = None,
) -> TransportPaths:
    home = _hermes_home(hermes_home)
    bridge = home / "hermes-agent" / "scripts" / "whatsapp-bridge" / "bridge.js"
    if not bridge.is_file():
        raise TransportError(f"Hermes WhatsApp bridge not found: {bridge}")

    bundled_nodes = sorted(
        (home / "tools").glob("node-*/bin/node"),
        key=lambda path: path.parent.parent.name,
    )
    node = bundled_nodes[-1] if bundled_nodes else None
    if node is None:
        external = shutil.which("node")
        if not external:
            raise TransportError("Node.js not found in Hermes tools or PATH")
        node = Path(external)
    if not node.is_file():
        raise TransportError(f"Node.js executable not found: {node}")

    candidates = [
        home / "whatsapp" / "session",
        home / "platforms" / "whatsapp" / "session",
    ]
    session = next((path for path in candidates if path.is_dir()), None)
    if session is None:
        raise TransportError(
            "paired WhatsApp session not found under "
            f"{candidates[0]} or {candidates[1]}"
        )

    return TransportPaths(home, node.absolute(), bridge.absolute(), session.absolute())


def hermes_whatsapp_enabled(*, hermes_home: str | Path | None = None) -> bool:
    env_path = _hermes_home(hermes_home) / ".env"
    if not env_path.is_file():
        return False
    value: str | None = None
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, current = line.split("=", 1)
        if key.strip() == "WHATSAPP_ENABLED":
            value = current.strip().strip('"').strip("'")
    return bool(value and value.lower() in {"true", "1", "yes", "on"})


def _bridge_port(config: AppConfig) -> int:
    return int(config.whatsapp["bridge_url"].rsplit(":", 1)[1])


def render_transport_units(
    config: AppConfig,
    *,
    python_executable: str | Path | None = None,
    hermes_home: str | Path | None = None,
) -> TransportUnits:
    paths = discover_transport_paths(hermes_home=hermes_home)
    python = Path(python_executable or sys.executable).expanduser()
    if not python.is_absolute():
        python = (Path.cwd() / python).absolute()

    groups = tuple(workflow.group_jid for workflow in config.workflows)
    if not groups:
        raise TransportError("at least one configured workflow group is required")
    group_csv = ",".join(groups)
    port = _bridge_port(config)

    bridge_text = "\n".join(
        [
            "[Unit]",
            "Description=WhatsApp Technical Digest - dedicated read bridge",
            "Wants=network-online.target",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_escape_unit_path(paths.bridge_script.parent)}",
            'Environment="WHATSAPP_GROUP_POLICY=allowlist"',
            f'Environment="WHATSAPP_GROUP_ALLOWED_USERS={group_csv}"',
            (
                "ExecStart="
                f"{_quote_exec(paths.node)} {_quote_exec(paths.bridge_script)} "
                f"--port {port} --session {_quote_exec(paths.session_dir)} --mode bot"
            ),
            "Restart=on-failure",
            "RestartSec=5s",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )

    collector_text = "\n".join(
        [
            "[Unit]",
            "Description=WhatsApp Technical Digest - read-only collector",
            f"Requires={BRIDGE_SERVICE}",
            f"After={BRIDGE_SERVICE}",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_escape_unit_path(config.source_path.parent)}",
            (
                "ExecStart="
                f"{_quote_exec(python)} -m whatsapp_digest.cli "
                f"--config {_quote_exec(config.source_path)} collector run"
            ),
            "Restart=on-failure",
            "RestartSec=5s",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    return TransportUnits(bridge_text, collector_text, paths, groups)


def _active_or_raise(
    unit_name: str,
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    active = _run_systemctl(["is-active", unit_name], executor=executor, check=False)
    if active.returncode != 0 or (active.stdout or "").strip() != "active":
        detail = (active.stderr or active.stdout or "unit did not become active").strip()
        raise TransportError(f"{unit_name} is not active after install: {detail}")


def install_transport(
    config: AppConfig,
    *,
    unit_dir: str | Path | None = None,
    python_executable: str | Path | None = None,
    hermes_home: str | Path | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> TransportUnits:
    if hermes_whatsapp_enabled(hermes_home=hermes_home):
        raise TransportError(
            "Hermes gateway WhatsApp is still enabled. Set WHATSAPP_ENABLED=false "
            "in ~/.hermes/.env and restart hermes-gateway before installing v2 transport."
        )

    existing_bridge_active = (
        _unit_state(BRIDGE_SERVICE, "is-active", executor=executor) == "active"
    )
    if _bridge_health(config) != "unreachable" and not existing_bridge_active:
        raise TransportError(
            "the configured WhatsApp bridge endpoint is already responding outside "
            "the managed v2 service; stop the manual bridge before installing transport"
        )

    units = render_transport_units(
        config,
        python_executable=python_executable,
        hermes_home=hermes_home,
    )
    root = Path(unit_dir or systemd_user_dir())
    root.mkdir(parents=True, exist_ok=True)
    bridge_path = root / BRIDGE_SERVICE
    collector_path = root / COLLECTOR_SERVICE
    _write_unit(bridge_path, units.bridge_text)
    _write_unit(collector_path, units.collector_text)
    _verify_units([bridge_path, collector_path], executor=executor)

    _run_systemctl(["daemon-reload"], executor=executor)
    _run_systemctl(["enable", "--now", BRIDGE_SERVICE], executor=executor)
    _active_or_raise(BRIDGE_SERVICE, executor=executor)
    _wait_bridge_connected(config)
    _run_systemctl(["enable", "--now", COLLECTOR_SERVICE], executor=executor)
    _active_or_raise(COLLECTOR_SERVICE, executor=executor)
    return units


def _bridge_health(config: AppConfig) -> str:
    try:
        with urlopen(config.whatsapp["bridge_url"] + "/health", timeout=2) as response:
            if getattr(response, "status", 200) != 200:
                return f"http-{response.status}"
            return "connected" if b'"status":"connected"' in response.read() else "responding"
    except Exception:
        return "unreachable"


def _wait_bridge_connected(
    config: AppConfig,
    *,
    timeout_seconds: float = 20.0,
    interval_seconds: float = 0.25,
    health_check: Callable[[AppConfig], str] = _bridge_health,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last = "unreachable"
    while time.monotonic() < deadline:
        last = health_check(config)
        if last == "connected":
            return
        sleeper(interval_seconds)
    raise TransportError(
        f"{BRIDGE_SERVICE} did not become connected within "
        f"{timeout_seconds:g}s (last health: {last})"
    )


def linger_status(
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    try:
        result = executor(
            ["loginctl", "show-user", getpass.getuser(), "--property=Linger", "--value"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    value = (result.stdout or "").strip().lower()
    return value if value in {"yes", "no"} else "unknown"


def transport_status(
    config: AppConfig,
    *,
    hermes_home: str | Path | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, str]:
    return {
        "hermes_whatsapp": "enabled" if hermes_whatsapp_enabled(hermes_home=hermes_home) else "disabled",
        "bridge_enabled": _unit_state(BRIDGE_SERVICE, "is-enabled", executor=executor),
        "bridge_active": _unit_state(BRIDGE_SERVICE, "is-active", executor=executor),
        "bridge_health": _bridge_health(config),
        "collector_enabled": _unit_state(COLLECTOR_SERVICE, "is-enabled", executor=executor),
        "collector_active": _unit_state(COLLECTOR_SERVICE, "is-active", executor=executor),
        "linger": linger_status(executor=executor),
    }


def remove_transport(
    *,
    unit_dir: str | Path | None = None,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    root = Path(unit_dir or systemd_user_dir())
    removed: list[str] = []
    for unit_name in (COLLECTOR_SERVICE, BRIDGE_SERVICE):
        _run_systemctl(["disable", "--now", unit_name], executor=executor, check=False)
        path = root / unit_name
        if path.exists():
            path.unlink()
            removed.append(unit_name)
    _run_systemctl(["daemon-reload"], executor=executor)
    return removed
