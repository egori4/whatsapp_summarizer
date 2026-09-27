"""Hermes-side worker for one isolated digest model call.

This file is executed by the Python interpreter from the user's existing Hermes
installation. Request/response data is exchanged through owner-only temporary
JSON files so WhatsApp source text never appears in process arguments.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

_HERMES_ROOT = os.environ.get("HERMES_AGENT_ROOT", "").strip()
if _HERMES_ROOT and _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)

# Hermes source installs may re-exec from a legacy venv into the PM-managed
# interpreter. Bootstrap before importing any Hermes/third-party dependency so
# the committed dependency environment is activated in the relaunched process.
import hermes_bootstrap  # noqa: F401,E402


class WorkerError(RuntimeError):
    pass


def _load_hermes_config() -> dict[str, Any]:
    from hermes_cli.config import load_config
    config = load_config()
    return config if isinstance(config, dict) else {}


def resolve_model_settings(requested: dict[str, Any], hermes_config: dict[str, Any]) -> dict[str, str | None]:
    model_cfg = hermes_config.get("model", {})
    if isinstance(model_cfg, str):
        global_model = model_cfg.strip()
        global_provider = None
    elif isinstance(model_cfg, dict):
        global_model = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
        global_provider = str(model_cfg.get("provider") or "").strip().lower() or None
    else:
        global_model = ""
        global_provider = None

    agent_cfg = hermes_config.get("agent", {})
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
    global_reasoning = str(agent_cfg.get("reasoning_effort") or "").strip().lower() or "medium"

    req_provider = str(requested.get("provider") or "inherit").strip().lower()
    req_model = str(requested.get("name") or "inherit").strip()
    req_reasoning = str(requested.get("reasoning") or "inherit").strip().lower()

    model = global_model if req_model == "inherit" else req_model
    if not model:
        raise WorkerError("Hermes has no configured model and workflow model is inherit")

    provider_request = None if req_provider == "inherit" else req_provider
    reasoning = global_reasoning if req_reasoning == "inherit" else req_reasoning

    return {
        "requested_provider": provider_request,
        "configured_provider": global_provider,
        "model": model,
        "reasoning": reasoning,
    }


def build_agent_kwargs(runtime: dict[str, Any], resolved: dict[str, Any], system_prompt: str) -> dict[str, Any]:
    from hermes_constants import parse_reasoning_effort

    return {
        "model": resolved["model"],
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "api_mode": runtime.get("api_mode"),
        "acp_command": runtime.get("command"),
        "acp_args": list(runtime.get("args") or []),
        "credential_pool": runtime.get("credential_pool"),
        "max_iterations": 1,
        "reasoning_config": parse_reasoning_effort(str(resolved["reasoning"])),
        "enabled_toolsets": [],
        "quiet_mode": True,
        "skip_context_files": True,
        "skip_memory": True,
        "skip_background_review": True,
        "save_trajectories": False,
        "fallback_model": None,
        "ephemeral_system_prompt": system_prompt,
        "platform": "whatsapp-digest",
    }


def execute_request(request: dict[str, Any]) -> dict[str, Any]:
    from dotenv import load_dotenv
    from hermes_constants import get_hermes_home
    from hermes_cli.runtime_provider import resolve_runtime_provider, format_runtime_provider_error
    from run_agent import AIAgent

    hermes_home = Path(os.environ.get("HERMES_HOME") or get_hermes_home()).expanduser().resolve()
    env_path = hermes_home / ".env"
    if env_path.exists():
        try:
            load_dotenv(str(env_path), override=True, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(str(env_path), override=True, encoding="latin-1")

    config = _load_hermes_config()
    resolved = resolve_model_settings(request.get("model") or {}, config)

    try:
        runtime = resolve_runtime_provider(requested=resolved["requested_provider"])
    except Exception as exc:
        raise WorkerError(format_runtime_provider_error(exc)) from exc

    kwargs = build_agent_kwargs(runtime, resolved, str(request.get("system_prompt") or ""))
    agent = AIAgent(**kwargs)
    response = agent.chat(str(request.get("user_prompt") or ""))
    if not isinstance(response, str) or not response.strip():
        raise WorkerError("Hermes returned an empty response")

    return {
        "response": response,
        "provider": str(runtime.get("provider") or resolved.get("configured_provider") or "unknown"),
        "model": str(resolved["model"]),
        "reasoning": str(resolved["reasoning"]),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 2:
        print("usage: hermes_worker.py REQUEST_JSON RESULT_JSON", file=sys.stderr)
        return 2
    request_path = Path(args[0])
    result_path = Path(args[1])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise WorkerError("request must be a JSON object")
        result = {"ok": True, **execute_request(request)}
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    try:
        result_path.chmod(0o600)
    except OSError:
        pass
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
