"""Configuration loading and validation for WhatsApp Technical Digest v2."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import copy
import os
import re
import shutil
from string import Formatter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

_GROUP_JID_RE = re.compile(r"^[0-9]+(?:-[0-9]+)?@g\.us$")
_WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_REASONING = {"inherit", "none", "minimal", "low", "medium", "high", "xhigh"}
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUBJECT_FIELDS = {"date", "workflow_name", "workflow_id", "run_id"}
_DURATION_RE = re.compile(r"^(\d+)([mhd])$", re.IGNORECASE)

OPTIONAL_CONFIG_DEFAULTS: dict[str, Any] = {
    "context": {
        "enabled": True,
        "lookback": "48h",
        "max_messages": 100,
    },
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ConfigUpgradeResult:
    source_path: Path
    output_path: Path | None
    backup_path: Path | None
    added: tuple[str, ...]
    applied: bool


def _read_raw_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ConfigError(f"config file not found: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    return source, _mapping(raw, "config")


def _merge_missing_defaults(
    target: dict[str, Any],
    defaults: dict[str, Any],
    *,
    prefix: str = "",
    added: list[str] | None = None,
) -> list[str]:
    result = added if added is not None else []
    for key, default in defaults.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in target:
            target[key] = copy.deepcopy(default)
            if isinstance(default, dict):
                result.extend(f"{path}.{child}" for child in default)
            else:
                result.append(path)
            continue
        if isinstance(default, dict) and isinstance(target[key], dict):
            _merge_missing_defaults(target[key], default, prefix=path, added=result)
    return result


def missing_config_defaults(path: str | Path) -> tuple[str, ...]:
    _source, root = _read_raw_config(path)
    probe = copy.deepcopy(root)
    return tuple(_merge_missing_defaults(probe, OPTIONAL_CONFIG_DEFAULTS))


def _next_backup_path(source: Path) -> Path:
    candidate = source.with_name(source.name + ".bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = source.with_name(source.name + f".bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def upgrade_config(path: str | Path, *, apply: bool = False) -> ConfigUpgradeResult:
    # Validate the existing file first; older configs remain valid through runtime defaults.
    load_config(path)
    source, root = _read_raw_config(path)
    upgraded = copy.deepcopy(root)
    added = tuple(_merge_missing_defaults(upgraded, OPTIONAL_CONFIG_DEFAULTS))
    if not added:
        return ConfigUpgradeResult(source, None, None, (), apply)

    output = source.with_name(source.name + ".upgraded")
    output.write_text(
        yaml.safe_dump(upgraded, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    os.chmod(output, source.stat().st_mode & 0o777)
    # Validate the generated candidate before it can replace the active configuration.
    load_config(output)

    if not apply:
        return ConfigUpgradeResult(source, output, None, added, False)

    backup = _next_backup_path(source)
    shutil.copy2(source, backup)
    os.replace(output, source)
    return ConfigUpgradeResult(source, source, backup, added, True)


@dataclass(frozen=True)
class SectionConfig:
    id: str
    title: str
    guidance: str = ""


@dataclass(frozen=True)
class WorkflowConfig:
    id: str
    name: str
    group_jid: str
    timezone: str
    schedule: dict[str, Any] | None
    model: dict[str, str]
    instructions: str
    sections: tuple[SectionConfig, ...]
    email: dict[str, Any]


@dataclass(frozen=True)
class AppConfig:
    source_path: Path
    database_path: Path
    log_path: Path
    processed_raw_days: int
    log_days: int
    hermes: dict[str, Any]
    whatsapp: dict[str, Any]
    email: dict[str, Any]
    default_timezone: str
    context: dict[str, Any]
    workflows: tuple[WorkflowConfig, ...] = field(default_factory=tuple)

    def workflow(self, workflow_id: str) -> WorkflowConfig:
        for workflow in self.workflows:
            if workflow.id == workflow_id:
                return workflow
        raise ConfigError(f"unknown workflow: {workflow_id}")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _resolve(base: Path, value: Any, name: str) -> Path:
    raw = _nonempty(value, name)
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _timezone(value: Any, name: str) -> str:
    zone = _nonempty(value, name)
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"{name} is not a valid timezone: {zone}") from exc
    return zone


def _validate_schedule(value: Any, timezone: str, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    schedule = _mapping(value, name)
    kind = _nonempty(schedule.get("type"), f"{name}.type").lower()
    if kind not in {"daily", "weekly", "monthly"}:
        raise ConfigError(f"{name}.type must be daily, weekly, or monthly")
    at = _nonempty(schedule.get("at"), f"{name}.at")
    if not _TIME_RE.fullmatch(at):
        raise ConfigError(f"{name}.at must use HH:MM 24-hour format")
    result: dict[str, Any] = {"type": kind, "at": at, "timezone": _timezone(schedule.get("timezone", timezone), f"{name}.timezone")}
    if kind == "weekly":
        days = schedule.get("days")
        if not isinstance(days, list) or not days:
            raise ConfigError(f"{name}.days must be a non-empty list for weekly schedules")
        normalized = [str(day).lower() for day in days]
        if len(set(normalized)) != len(normalized) or any(day not in _DAYS for day in normalized):
            raise ConfigError(f"{name}.days contains an invalid or duplicate weekday")
        result["days"] = normalized
    elif kind == "monthly":
        day = schedule.get("day")
        if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31:
            raise ConfigError(f"{name}.day must be an integer from 1 to 31")
        result["day"] = day
    return result


def _validate_emails(values: Any, name: str, *, required: bool) -> list[str]:
    if values is None and not required:
        return []
    if not isinstance(values, list) or (required and not values):
        raise ConfigError(f"{name} must be {'a non-empty ' if required else ''}list")
    result = []
    for index, value in enumerate(values):
        email = _nonempty(value, f"{name}[{index}]")
        if not _EMAIL_RE.fullmatch(email):
            raise ConfigError(f"{name}[{index}] is not a valid email address")
        result.append(email)
    return result


def _duration_seconds(value: Any, name: str) -> tuple[str, int]:
    raw = _nonempty(value, name).lower()
    match = _DURATION_RE.fullmatch(raw)
    if not match or int(match.group(1)) <= 0:
        raise ConfigError(f"{name} must use a positive duration such as 24h, 48h, or 3d")
    amount = int(match.group(1))
    seconds = amount * {"m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]
    return raw, seconds


def _validate_subject_template(value: Any, name: str) -> str:
    template = _nonempty(value, name)
    if "\r" in template or "\n" in template:
        raise ConfigError(f"{name} must be a single line")
    try:
        fields = [field for _literal, field, spec, conversion in Formatter().parse(template) if field]
    except ValueError as exc:
        raise ConfigError(f"{name} is not a valid format string") from exc
    for field in fields:
        if field not in _SUBJECT_FIELDS:
            raise ConfigError(f"{name} contains unsupported placeholder: {field}")
    return template


def load_config(path: str | Path) -> AppConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ConfigError(f"config file not found: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    root = _mapping(raw, "config")
    base = source.parent

    paths = _mapping(root.get("paths"), "paths")
    database_path = _resolve(base, paths.get("database"), "paths.database")
    log_path = _resolve(base, paths.get("log"), "paths.log")

    retention = _mapping(root.get("retention", {}), "retention")
    processed_raw_days = retention.get("processed_raw_days", 7)
    log_days = retention.get("log_days", 30)
    for key, value in (("processed_raw_days", processed_raw_days), ("log_days", log_days)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"retention.{key} must be a non-negative integer")

    hermes = _mapping(root.get("hermes", {}), "hermes")
    command = _nonempty(hermes.get("command", "hermes"), "hermes.command")
    timeout = hermes.get("timeout_seconds", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ConfigError("hermes.timeout_seconds must be a positive integer")
    hermes = {"command": command, "timeout_seconds": timeout}

    whatsapp = _mapping(root.get("whatsapp", {}), "whatsapp")
    bridge_url = _nonempty(whatsapp.get("bridge_url", "http://127.0.0.1:3001"), "whatsapp.bridge_url").rstrip("/")
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", bridge_url):
        raise ConfigError("whatsapp.bridge_url must be an explicit loopback URL such as http://127.0.0.1:3001")
    bridge_port = int(bridge_url.rsplit(":", 1)[1])
    if not 1 <= bridge_port <= 65535:
        raise ConfigError("whatsapp.bridge_url port must be between 1 and 65535")
    poll_interval = whatsapp.get("poll_interval_seconds", 1.0)
    if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) or not 0.1 <= float(poll_interval) <= 60:
        raise ConfigError("whatsapp.poll_interval_seconds must be between 0.1 and 60")
    whatsapp = {"bridge_url": bridge_url, "poll_interval_seconds": float(poll_interval)}

    email_raw = _mapping(root.get("email"), "email")
    host = _nonempty(email_raw.get("host"), "email.host")
    sender = _nonempty(email_raw.get("sender"), "email.sender")
    if not _EMAIL_RE.fullmatch(sender):
        raise ConfigError("email.sender must be a valid email address")
    password_env = _nonempty(email_raw.get("password_env"), "email.password_env")
    if not _ENV_RE.fullmatch(password_env):
        raise ConfigError("email.password_env must be a valid environment variable name")
    username_raw = email_raw.get("username")
    username = None if username_raw is None else _nonempty(username_raw, "email.username")
    port = email_raw.get("port", 587)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("email.port must be between 1 and 65535")
    starttls = email_raw.get("starttls", True)
    if not isinstance(starttls, bool):
        raise ConfigError("email.starttls must be boolean")
    email_timeout = email_raw.get("timeout_seconds", 30)
    if not isinstance(email_timeout, int) or isinstance(email_timeout, bool) or email_timeout <= 0:
        raise ConfigError("email.timeout_seconds must be a positive integer")
    email = {
        "host": host,
        "port": port,
        "sender": sender,
        "username": username,
        "password_env": password_env,
        "starttls": starttls,
        "timeout_seconds": email_timeout,
    }

    defaults = _mapping(root.get("defaults", {}), "defaults")
    default_timezone = _timezone(defaults.get("timezone", "America/Toronto"), "defaults.timezone")

    context_defaults = OPTIONAL_CONFIG_DEFAULTS["context"]
    context_raw = _mapping(root.get("context", {}), "context")
    context_enabled = context_raw.get("enabled", context_defaults["enabled"])
    if not isinstance(context_enabled, bool):
        raise ConfigError("context.enabled must be boolean")
    context_lookback, context_seconds = _duration_seconds(context_raw.get("lookback", context_defaults["lookback"]), "context.lookback")
    context_max_messages = context_raw.get("max_messages", context_defaults["max_messages"])
    if not isinstance(context_max_messages, int) or isinstance(context_max_messages, bool) or not 1 <= context_max_messages <= 1000:
        raise ConfigError("context.max_messages must be an integer from 1 to 1000")
    if context_enabled and context_seconds > processed_raw_days * 86400:
        raise ConfigError(
            f"context.lookback ({context_lookback}) exceeds retention.processed_raw_days ({processed_raw_days}d)"
        )
    context = {
        "enabled": context_enabled,
        "lookback": context_lookback,
        "lookback_seconds": context_seconds,
        "max_messages": context_max_messages,
    }

    workflows_raw = root.get("workflows")
    if not isinstance(workflows_raw, list) or not workflows_raw:
        raise ConfigError("workflows must be a non-empty list")

    seen_ids: set[str] = set()
    seen_jids: set[str] = set()
    workflows: list[WorkflowConfig] = []
    for index, item in enumerate(workflows_raw):
        prefix = f"workflows[{index}]"
        workflow = _mapping(item, prefix)
        workflow_id = _nonempty(workflow.get("id"), f"{prefix}.id")
        if not _WORKFLOW_ID_RE.fullmatch(workflow_id):
            raise ConfigError(
                f"{prefix}.id must use only letters, numbers, dot, underscore, or hyphen"
            )
        if workflow_id in seen_ids:
            raise ConfigError(f"duplicate workflow id: {workflow_id}")
        seen_ids.add(workflow_id)
        group_jid = _nonempty(workflow.get("group_jid"), f"{prefix}.group_jid")
        if not _GROUP_JID_RE.fullmatch(group_jid):
            raise ConfigError(f"{prefix}.group_jid is not a valid WhatsApp group JID")
        if group_jid in seen_jids:
            raise ConfigError(f"duplicate group JID: {group_jid}")
        seen_jids.add(group_jid)
        timezone = _timezone(workflow.get("timezone", default_timezone), f"{prefix}.timezone")
        schedule = _validate_schedule(workflow.get("schedule"), timezone, f"{prefix}.schedule")

        model_raw = _mapping(workflow.get("model", {}), f"{prefix}.model")
        model = {
            "provider": _nonempty(model_raw.get("provider", "inherit"), f"{prefix}.model.provider"),
            "name": _nonempty(model_raw.get("name", "inherit"), f"{prefix}.model.name"),
            "reasoning": _nonempty(model_raw.get("reasoning", "inherit"), f"{prefix}.model.reasoning").lower(),
        }
        if model["reasoning"] not in _REASONING:
            raise ConfigError(f"{prefix}.model.reasoning is unsupported")

        summarization = _mapping(workflow.get("summarization", {}), f"{prefix}.summarization")
        instructions = str(summarization.get("instructions", "")).strip()
        sections_raw = summarization.get("sections", [])
        if sections_raw is None:
            sections_raw = []
        if not isinstance(sections_raw, list):
            raise ConfigError(f"{prefix}.summarization.sections must be a list")
        section_ids: set[str] = set()
        sections: list[SectionConfig] = []
        for section_index, section_value in enumerate(sections_raw):
            section_name = f"{prefix}.summarization.sections[{section_index}]"
            section = _mapping(section_value, section_name)
            section_id = _nonempty(section.get("id"), f"{section_name}.id")
            if section_id in section_ids:
                raise ConfigError(f"duplicate section id in {workflow_id}: {section_id}")
            section_ids.add(section_id)
            sections.append(SectionConfig(section_id, _nonempty(section.get("title"), f"{section_name}.title"), str(section.get("guidance", "")).strip()))

        delivery = _mapping(workflow.get("delivery"), f"{prefix}.delivery")
        workflow_email = _mapping(delivery.get("email"), f"{prefix}.delivery.email")
        to = _validate_emails(workflow_email.get("to"), f"{prefix}.delivery.email.to", required=True)
        cc = _validate_emails(workflow_email.get("cc", []), f"{prefix}.delivery.email.cc", required=False)
        attach_raw = workflow_email.get("attach_raw_messages", True)
        if not isinstance(attach_raw, bool):
            raise ConfigError(f"{prefix}.delivery.email.attach_raw_messages must be boolean")
        subject = _validate_subject_template(
            workflow_email.get("subject", "{workflow_name} — {date}"),
            f"{prefix}.delivery.email.subject",
        )

        workflows.append(WorkflowConfig(
            id=workflow_id,
            name=_nonempty(workflow.get("name", workflow_id), f"{prefix}.name"),
            group_jid=group_jid,
            timezone=timezone,
            schedule=schedule,
            model=model,
            instructions=instructions,
            sections=tuple(sections),
            email={"to": to, "cc": cc, "attach_raw_messages": attach_raw, "subject": subject},
        ))

    return AppConfig(source, database_path, log_path, processed_raw_days, log_days, hermes, whatsapp, email, default_timezone, context, tuple(workflows))
