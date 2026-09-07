from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import HTTPDefaultErrorHandler, HTTPErrorProcessor, HTTPHandler, OpenerDirector, ProxyHandler, Request
from urllib.parse import urlsplit

from .models import ModelFailure


def _loopback_opener() -> OpenerDirector:
    """Build an HTTP-only loopback transport with neither proxies nor redirects."""
    opener = OpenerDirector()
    opener.add_handler(ProxyHandler({}))
    opener.add_handler(HTTPHandler())
    opener.add_handler(HTTPDefaultErrorHandler())
    opener.add_handler(HTTPErrorProcessor())
    return opener


class OllamaLocalModel:
    """Minimal local-only Ollama `/api/generate` adapter with injectable I/O for tests."""

    def __init__(self, model: str, *, endpoint: str = "http://127.0.0.1:11434/api/generate", timeout: int = 120, max_output_tokens: int = 1024, opener: Callable | None = None) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ollama endpoint must be an explicit loopback HTTP URL")
        if parsed.path in {"", "/"}:
            endpoint = f"{endpoint.rstrip('/')}/api/generate"
        elif parsed.path != "/api/generate":
            raise ValueError("Ollama endpoint path must be /api/generate")
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        self.max_output_tokens = max_output_tokens
        self.opener = opener or _loopback_opener().open

    def complete(self, prompt: str) -> str:
        return self._request(prompt)

    def complete_structured(self, prompt: str, schema: dict[str, Any]) -> str:
        if not isinstance(schema, dict):
            raise ValueError("schema must be an object")
        return self._request(prompt, schema=schema)

    def _request(self, prompt: str, *, schema: dict[str, Any] | None = None) -> str:
        payload_data: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"num_predict": self.max_output_tokens, "temperature": 0},
        }
        if schema is not None:
            payload_data["format"] = schema
        payload = json.dumps(payload_data).encode("utf-8")
        request = Request(self.endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except (OSError, URLError, ValueError) as exc:
            raise ModelFailure(f"local Ollama request failed: {type(exc).__name__}") from exc
        text = result.get("response")
        if not isinstance(text, str):
            raise ModelFailure("local Ollama response did not contain text")
        return text
