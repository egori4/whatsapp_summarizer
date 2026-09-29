"""Native Ollama structured-output gateway for explicitly selected local workflows."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import yaml

from ..config import AppConfig, WorkflowConfig
from .hermes import (
    BASE_SYSTEM_PROMPT,
    MAX_OUTPUT_CHARS,
    HermesGatewayError,
    ModelInvocationResult,
    fit_prompt_context,
    source_record,
)


logger = logging.getLogger(__name__)
OLLAMA_PROVIDER = "ollama-local"
_MAX_ENVELOPE_BYTES = MAX_OUTPUT_CHARS * 4


def _hermes_config_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser().resolve()
    return home / "config.yaml"


def _load_ollama_api() -> str:
    path = _hermes_config_path()
    if not path.is_file():
        raise HermesGatewayError(f"Hermes config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HermesGatewayError(f"failed to read Hermes config: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise HermesGatewayError("Hermes config must be a mapping")
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        raise HermesGatewayError("Hermes config has no providers mapping")
    provider = providers.get(OLLAMA_PROVIDER)
    if not isinstance(provider, dict):
        raise HermesGatewayError(f"Hermes provider '{OLLAMA_PROVIDER}' is not configured")
    api = provider.get("api")
    if not isinstance(api, str) or not api.strip():
        raise HermesGatewayError(f"Hermes provider '{OLLAMA_PROVIDER}' has no api URL")
    return api.strip()


def _native_chat_url(api_url: str) -> str:
    parts = urlsplit(api_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise HermesGatewayError("Ollama provider api must be an http(s) URL with a host")
    if parts.query or parts.fragment:
        raise HermesGatewayError("Ollama provider api must not contain a query string or fragment")
    return urlunsplit((parts.scheme, parts.netloc, "/api/chat", "", ""))


def build_output_schema(workflow: WorkflowConfig) -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    item_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "details": string_array,
            "actions": string_array,
            "questions": string_array,
            "reported_by": string_array,
            "contributors": string_array,
            "references": string_array,
            "source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": [
            "title", "summary", "details", "actions", "questions",
            "reported_by", "contributors", "references", "source_ids",
        ],
        "additionalProperties": False,
    }
    section_id: dict[str, Any] = {"type": "string"}
    if workflow.sections:
        section_id["enum"] = [section.id for section in workflow.sections]
    section_schema = {
        "type": "object",
        "properties": {
            "section_id": section_id,
            "title": {"type": "string"},
            "items": {"type": "array", "items": item_schema},
        },
        "required": ["section_id", "title", "items"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "sections": {"type": "array", "items": section_schema},
        },
        "required": ["sections"],
        "additionalProperties": False,
    }


def _resolve_model(workflow: WorkflowConfig) -> tuple[str, str]:
    model = str(workflow.model.get("name") or "").strip()
    if not model or model == "inherit":
        raise HermesGatewayError("ollama-local requires a concrete workflow model name")
    reasoning = str(workflow.model.get("reasoning") or "inherit").strip().lower()
    if reasoning not in {"none", "inherit"}:
        raise HermesGatewayError(
            "ollama-local structured output supports reasoning 'none' or 'inherit' only"
        )
    return model, "none"


class OllamaStructuredGateway:
    def __init__(self, config: AppConfig):
        self.config = config

    def summarize(
        self,
        workflow: WorkflowConfig,
        rows: list[Any],
        *,
        context_rows: list[Any] | None = None,
    ) -> ModelInvocationResult:
        records = [
            source_record(row)
            for row in rows
            if (row["text"] or row["caption"]) and not row["deleted"]
        ]
        if not records:
            raise HermesGatewayError("no usable source messages supplied to model gateway")
        context_records = [
            source_record(row)
            for row in (context_rows or [])
            if (row["text"] or row["caption"]) and not row["deleted"]
        ]
        user_prompt, context_records = fit_prompt_context(workflow, records, context_records)
        model, reasoning = _resolve_model(workflow)
        endpoint = _native_chat_url(_load_ollama_api())
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": BASE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "format": build_output_schema(workflow),
            "options": {"temperature": 0},
        }
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=int(self.config.hermes["timeout_seconds"])) as response:
                body = response.read(_MAX_ENVELOPE_BYTES + 1)
        except HTTPError as exc:
            raise HermesGatewayError(f"Ollama HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise HermesGatewayError("Ollama connection failed") from exc
        except TimeoutError as exc:
            raise HermesGatewayError("Ollama model call timed out") from exc
        except OSError as exc:
            raise HermesGatewayError(f"Ollama request failed: {type(exc).__name__}") from exc
        elapsed = time.monotonic() - started
        logger.info(
            "provider=ollama-local model=%s reasoning=none structured_output=true generation_seconds=%.3f",
            model,
            elapsed,
        )
        if len(body) > _MAX_ENVELOPE_BYTES:
            raise HermesGatewayError("Ollama response envelope exceeded safe size")
        try:
            envelope = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HermesGatewayError("Ollama returned an invalid JSON response envelope") from exc
        if not isinstance(envelope, dict):
            raise HermesGatewayError("Ollama response envelope must be a JSON object")
        message = envelope.get("message")
        if not isinstance(message, dict):
            raise HermesGatewayError("Ollama response is missing message")
        output = message.get("content")
        if not isinstance(output, str) or not output.strip():
            raise HermesGatewayError("Ollama returned empty model output")
        if len(output) > MAX_OUTPUT_CHARS:
            raise HermesGatewayError("Ollama model output exceeded safe output size")
        return ModelInvocationResult(
            raw_output=output,
            provider=OLLAMA_PROVIDER,
            model=model,
            reasoning=reasoning,
            source_records=tuple(records),
            context_source_records=tuple(context_records),
        )
