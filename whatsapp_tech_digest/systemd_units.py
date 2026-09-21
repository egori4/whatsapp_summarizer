from __future__ import annotations

from pathlib import Path
from shlex import quote

SERVICE_NAME = "whatsapp-tech-digest"


def _value(value: str | Path) -> str:
    text = str(value)
    if not text or "\n" in text or "\r" in text:
        raise ValueError("systemd unit arguments must be nonempty single-line values")
    return quote(text)


def build_service_unit(
    *,
    project_root: str | Path,
    policy_path: str | Path,
    spool_path: str | Path,
    lock_path: str | Path,
    credential_source: str | Path,
    credential_key: str,
) -> str:
    """Build but do not install the hardened unattended-delivery service unit."""
    root = Path(project_root)
    state_dir = Path(spool_path).parent
    python = root / ".venv" / "bin" / "python"
    command = " ".join(
        [
            _value(python), "-m", "whatsapp_tech_digest.unattended",
            "--policy", _value(policy_path),
            "--spool", _value(spool_path),
            "--lock", _value(lock_path),
            "--credential-source", _value(credential_source),
            "--credential-key", _value(credential_key),
        ]
    )
    return f"""[Unit]
Description=WhatsApp Tech Digest unattended delivery (approval-gated)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={_value(root)}
ExecStart={command}
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallArchitectures=native
ReadWritePaths={_value(state_dir)}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
"""


def build_timer_unit() -> str:
    """Build but do not install the fixed 07:30 Toronto timer unit."""
    return f"""[Unit]
Description=Run WhatsApp Tech Digest at 07:30 Toronto time

[Timer]
OnCalendar=*-*-* 07:30:00 America/Toronto
Persistent=false
Unit={SERVICE_NAME}.service

[Install]
WantedBy=timers.target
"""
