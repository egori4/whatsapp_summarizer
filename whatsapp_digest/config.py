"""Configuration loading and validation for WhatsApp Technical Digest v2."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

_GROUP_JID_RE = re.compile(r"^[0-9]+(?:-[0-9]+)?@g\.us$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
_REASONING = {"inherit", "none", "minimal", "low", "medium", "high", "xhigh"}


class ConfigError(ValueError):
    pass


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
    bridge_url = _nonempty(whatsapp.get("bridge_url", "http://127.0.0.1:3000"), "whatsapp.bridge_url").rstrip("/")
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", bridge_url):
        raise ConfigError("whatsapp.bridge_url must be an explicit loopback URL such as http://127.0.0.1:3000")
    bridge_port = int(bridge_url.rsplit(":", 1)[1])
    if not 1 <= bridge_port <= 65535:
        raise ConfigError("whatsapp.bridge_url port must be between 1 and 65535")
    poll_interval = whatsapp.get("poll_interval_seconds", 1.0)
    if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) or not 0.1 <= float(poll_interval) <= 60:
        raise ConfigError("whatsapp.poll_interval_seconds must be between 0.1 and 60")
    whatsapp = {"bridge_url": bridge_url, "poll_interval_seconds": float(poll_interval)}

    email = _mapping(root.get("email"), "email")
    for key in ("host", "sender", "password_env"):
        _nonempty(email.get(key), f"email.{key}")
    port = email.get("port", 587)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("email.port must be between 1 and 65535")

    defaults = _mapping(root.get("defaults", {}), "defaults")
    default_timezone = _timezone(defaults.get("timezone", "America/Toronto"), "defaults.timezone")

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
        subject = _nonempty(workflow_email.get("subject", "{workflow_name} — {date}"), f"{prefix}.delivery.email.subject")

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

    return AppConfig(source, database_path, log_path, processed_raw_days, log_days, hermes, whatsapp, email, default_timezone, tuple(workflows))
