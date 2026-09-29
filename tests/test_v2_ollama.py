import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from contextlib import contextmanager

import pytest
import yaml

from whatsapp_digest.config import SectionConfig, WorkflowConfig
from whatsapp_digest.model.hermes import HermesGatewayError
from whatsapp_digest.model.ollama import (
    OllamaStructuredGateway,
    _native_chat_url,
    build_output_schema,
)


def workflow(model=None, sections=None):
    return WorkflowConfig(
        id="tests",
        name="Tests",
        group_jid="120363429012626317@g.us",
        timezone="America/Toronto",
        schedule=None,
        model=model or {"provider": "ollama-local", "name": "qwen3.5:9b", "reasoning": "none"},
        instructions="Focus on technical updates.",
        sections=sections if sections is not None else (
            SectionConfig("technical", "Technical Updates", "Technical material."),
        ),
        email={"to": ["user@example.com"], "cc": [], "attach_raw_messages": True, "subject": "Test"},
    )


ROW = {
    "message_id": "m1",
    "occurred_at": "2026-09-27T12:00:00+00:00",
    "display_name": "Rahul",
    "text": "Upgrade to 10.6.2 resolves the issue.",
    "caption": None,
    "deleted": 0,
}
class Cfg:
    hermes = {"timeout_seconds": 5}


@contextmanager
def fake_ollama(response_content='{"sections":[]}', *, status=200, raw_body=None):
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            seen["path"] = self.path
            seen["payload"] = json.loads(self.rfile.read(length).decode("utf-8"))
            body = raw_body if raw_body is not None else json.dumps({"message": {"content": response_content}}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def write_hermes_config(tmp_path: Path, port: int, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "providers": {
                "ollama-local": {
                    "api": f"http://127.0.0.1:{port}/v1",
                    "transport": "chat_completions",
                    "models": ["something-else"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
def test_native_chat_url_uses_origin_and_api_chat():
    assert _native_chat_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/api/chat"
    assert _native_chat_url("https://example.test/custom/path") == "https://example.test/api/chat"


@pytest.mark.parametrize("url", [
    "ftp://127.0.0.1:11434/v1",
    "http:///v1",
    "http://127.0.0.1:11434/v1?x=1",
    "http://127.0.0.1:11434/v1#fragment",
])
def test_native_chat_url_rejects_invalid_provider_urls(url):
    with pytest.raises(HermesGatewayError):
        _native_chat_url(url)


def test_schema_constrains_configured_sections_and_item_shape():
    schema = build_output_schema(workflow())
    section = schema["properties"]["sections"]["items"]
    assert section["properties"]["section_id"]["enum"] == ["technical"]
    assert section["additionalProperties"] is False
    item = section["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert item["properties"]["references"] == {"type": "array", "items": {"type": "string"}}
    assert item["properties"]["source_ids"]["minItems"] == 1
    assert set(item["required"]) == set(item["properties"])
    dumped = json.dumps(schema)
    assert "m1" not in dumped
    assert "Rahul" not in dumped


def test_schema_allows_dynamic_section_id_when_unconfigured():
    schema = build_output_schema(workflow(sections=()))
    section_id = schema["properties"]["sections"]["items"]["properties"]["section_id"]
    assert section_id == {"type": "string"}
def test_gateway_sends_native_structured_request(monkeypatch, tmp_path):
    with fake_ollama() as (port, seen):
        write_hermes_config(tmp_path, port, monkeypatch)
        result = OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])

    assert seen["path"] == "/api/chat"
    payload = seen["payload"]
    assert payload["model"] == "qwen3.5:9b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0}
    assert payload["format"]["properties"]["sections"]
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert "CURRENT MESSAGES" in payload["messages"][1]["content"]
    assert result.raw_output == '{"sections":[]}'
    assert result.provider == "ollama-local"
    assert result.model == "qwen3.5:9b"
    assert result.reasoning == "none"
    assert result.source_records[0]["source_id"] == "m1"


def test_gateway_reasoning_inherit_resolves_to_none(monkeypatch, tmp_path):
    with fake_ollama() as (port, _seen):
        write_hermes_config(tmp_path, port, monkeypatch)
        wf = workflow({"provider": "ollama-local", "name": "qwen3.5:9b", "reasoning": "inherit"})
        result = OllamaStructuredGateway(Cfg()).summarize(wf, [ROW])
    assert result.reasoning == "none"


@pytest.mark.parametrize("model,reasoning,match", [
    ("inherit", "none", "concrete"),
    ("qwen3.5:9b", "medium", "reasoning"),
])
def test_gateway_rejects_unsupported_model_settings_before_http(
    monkeypatch, tmp_path, model, reasoning, match
):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("providers: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    wf = workflow({"provider": "ollama-local", "name": model, "reasoning": reasoning})
    with pytest.raises(HermesGatewayError, match=match):
        OllamaStructuredGateway(Cfg()).summarize(wf, [ROW])
def test_gateway_does_not_enforce_provider_model_allowlist(monkeypatch, tmp_path):
    with fake_ollama() as (port, seen):
        write_hermes_config(tmp_path, port, monkeypatch)
        result = OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])
    assert seen["payload"]["model"] == "qwen3.5:9b"
    assert result.model == "qwen3.5:9b"


def test_gateway_timeout_is_model_error(monkeypatch, tmp_path):
    import whatsapp_digest.model.ollama as ollama_module

    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "providers:\n  ollama-local:\n    api: http://127.0.0.1:11434/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        ollama_module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    with pytest.raises(HermesGatewayError, match="timed out"):
        OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])


def test_gateway_http_error_is_model_error(monkeypatch, tmp_path):
    with fake_ollama(status=503) as (port, _seen):
        write_hermes_config(tmp_path, port, monkeypatch)
        with pytest.raises(HermesGatewayError, match="HTTP error: 503"):
            OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])


def test_gateway_invalid_response_envelope_fails_clearly(monkeypatch, tmp_path):
    with fake_ollama(raw_body=b"not-json") as (port, _seen):
        write_hermes_config(tmp_path, port, monkeypatch)
        with pytest.raises(HermesGatewayError, match="invalid JSON response envelope"):
            OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])


def test_gateway_empty_model_content_fails_clearly(monkeypatch, tmp_path):
    with fake_ollama(response_content="") as (port, _seen):
        write_hermes_config(tmp_path, port, monkeypatch)
        with pytest.raises(HermesGatewayError, match="empty model output"):
            OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])


def test_gateway_missing_provider_config_fails_without_source_leak(monkeypatch, tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("providers: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    with pytest.raises(HermesGatewayError) as exc:
        OllamaStructuredGateway(Cfg()).summarize(workflow(), [ROW])
    assert "Upgrade to 10.6.2" not in str(exc.value)
