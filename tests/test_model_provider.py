from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from whatsapp_tech_digest.config import ConfigError, DigestConfig
from whatsapp_tech_digest.models import ModelFailure
from whatsapp_tech_digest.model_provider import AnthropicMessagesModel, HermesOpenAICodexModel, OpenAIChatModel, build_model


class ModelProviderTests(unittest.TestCase):
    def policy(self, provider: str, endpoint: str, api_key_env: str | None) -> DigestConfig:
        data = json.loads((Path(__file__).parents[1] / "config" / "digest.policy.example.json").read_text())
        data["models"].update({
            "provider": provider,
            "endpoint": endpoint,
            "api_key_env": api_key_env,
            "preclassifier": "provider-classifier",
            "final": "provider-final",
            "fallback": "provider-fallback",
        })
        return DigestConfig.from_dict(data)

    def test_provider_selection_is_policy_driven_without_storing_a_secret(self) -> None:
        config = self.policy("openai", "https://api.openai.com/v1/chat/completions", "DIGEST_OPENAI_API_KEY")
        self.assertEqual(config.models.provider, "openai")
        self.assertEqual(config.models.api_key_env, "DIGEST_OPENAI_API_KEY")
        self.assertIsInstance(build_model(config.models, "final"), OpenAIChatModel)

        config = self.policy("anthropic", "https://api.anthropic.com/v1/messages", "DIGEST_ANTHROPIC_API_KEY")
        self.assertIsInstance(build_model(config.models, "preclassifier"), AnthropicMessagesModel)

        config = self.policy("hermes-openai-codex", "local://hermes-cli", None)
        self.assertIsInstance(build_model(config.models, "final"), HermesOpenAICodexModel)
        self.assertIsNone(config.models.api_key_env)

    def test_hermes_codex_policy_requires_local_connector_without_api_key_reference(self) -> None:
        with self.assertRaises(ConfigError):
            self.policy("hermes-openai-codex", "https://api.openai.com/v1/chat/completions", None)
        with self.assertRaises(ConfigError):
            self.policy("hermes-openai-codex", "local://hermes-cli", "DIGEST_OPENAI_API_KEY")

    def test_hermes_codex_policy_pins_reasoning_effort(self) -> None:
        data = json.loads((Path(__file__).parents[1] / "config" / "digest.policy.example.json").read_text())
        data["models"].update({
            "provider": "hermes-openai-codex",
            "endpoint": "local://hermes-cli",
            "preclassifier": "provider-classifier",
            "final": "provider-final",
            "fallback": "provider-fallback",
            "reasoning_effort": "high",
        })
        config = DigestConfig.from_dict(data)
        self.assertEqual(config.models.reasoning_effort, "high")
        data["models"]["reasoning_effort"] = "inherited"
        with self.assertRaises(ConfigError):
            DigestConfig.from_dict(data)

    def test_hermes_codex_policy_requires_explicit_character_budgets(self) -> None:
        data = json.loads((Path(__file__).parents[1] / "config" / "digest.policy.example.json").read_text())
        data["models"].pop("max_output_chars", None)
        with self.assertRaises(ConfigError):
            DigestConfig.from_dict(data)

    def test_live_hermes_codex_policy_has_no_local_artifact_digest_requirement(self) -> None:
        data = json.loads((Path(__file__).parents[1] / "config" / "digest.policy.example.json").read_text())
        data["example_only"] = False
        data["whatsapp"]["bridge_port"] = 17778
        data["paths"] = {"spool": "/tmp/wtd-test/spool.sqlite3", "state_dir": "/tmp/wtd-test", "mode": "0700"}
        data["smtp"].update({"host": "smtp.invalid.test", "sender": "digest@invalid.test"})
        data["models"].update({
            "provider": "hermes-openai-codex", "pipeline_mode": "one_pass",
            "preclassifier": "gpt-5.6-terra", "final": "gpt-5.6-terra", "fallback": "gpt-5.6-terra",
            "endpoint": "local://hermes-cli", "reasoning_effort": "high",
        })
        for key in ("preclassifier_digest", "final_digest", "fallback_digest"):
            data["models"].pop(key)

        config = DigestConfig.from_dict(data)
        config.require_live_safe()
        self.assertEqual(config.models.final, "gpt-5.6-terra")

    def test_hermes_codex_adapter_rejects_oversized_prompt_before_runner(self) -> None:
        calls = []
        model = HermesOpenAICodexModel("gpt-5.6-terra", timeout=17, max_input_bytes=12, max_output_chars=64, reasoning_effort="high", runner=lambda *args, **kwargs: calls.append((args, kwargs)))

        with self.assertRaises(ModelFailure):
            model.complete("source-too-large")

        self.assertEqual(calls, [])

    def test_hermes_codex_adapter_reports_when_schema_pushes_structured_input_over_budget(self) -> None:
        calls = []
        model = HermesOpenAICodexModel(
            "gpt-5.6-terra", timeout=17, max_input_bytes=100, max_output_chars=64,
            reasoning_effort="high", runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        with self.assertRaisesRegex(ModelFailure, "including response schema"):
            model.complete_structured("x" * 70, {"type": "object", "properties": {"value": {"type": "string"}}})

        self.assertEqual(calls, [])

    def test_hermes_codex_adapter_rejects_oversized_response_before_parsing(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "x" * 65, "")

        model = HermesOpenAICodexModel("gpt-5.6-terra", timeout=17, max_input_bytes=128, max_output_chars=64, reasoning_effort="high", runner=runner)
        with self.assertRaises(ModelFailure):
            model.complete("bounded source")

    def test_hermes_codex_adapter_uses_owner_only_file_and_an_empty_toolset(self) -> None:
        captured = {}

        def runner(command, **kwargs):
            prompt_path = Path(command[command.index("--query-file") + 1])
            captured["command"] = command
            captured["mode"] = prompt_path.stat().st_mode & 0o777
            captured["prompt"] = prompt_path.read_text(encoding="utf-8")
            captured["timeout"] = kwargs["timeout"]
            return subprocess.CompletedProcess(command, 0, '{"rows":[]}', "")

        model = HermesOpenAICodexModel("gpt-5.6-terra", timeout=17, max_input_bytes=4096, max_output_chars=1024, reasoning_effort="high", runner=runner)
        self.assertEqual(model.complete_structured("Classify this source", {"type": "object"}), '{"rows":[]}')

        command = captured["command"]
        self.assertEqual(command[:2], ["hermes", "chat"])
        self.assertEqual(command[command.index("--toolsets") + 1], "context_engine")
        self.assertEqual(command[command.index("--provider") + 1], "openai-codex")
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-terra")
        self.assertEqual(command[command.index("--reasoning") + 1], "high")
        self.assertIn("--safe-mode", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--oneshot", command)
        self.assertIn("--quiet", command)
        self.assertEqual(captured["mode"], 0o600)
        self.assertIn("Classify this source", captured["prompt"])
        self.assertIn('"type":"object"', captured["prompt"])
        self.assertNotIn("fixture-secret", " ".join(command))
        self.assertEqual(captured["timeout"], 17)
        self.assertFalse(Path(command[command.index("--query-file") + 1]).exists())

    def test_openai_adapter_requests_json_schema_without_exposing_api_key(self) -> None:
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"choices":[{"message":{"content":"{\\"sections\\":[]}"}}]}'

        def opener(request, timeout):
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return Response()

        with patch.dict(os.environ, {"DIGEST_OPENAI_API_KEY": "fixture-secret"}, clear=False):
            model = OpenAIChatModel("model-id", endpoint="https://api.openai.com/v1/chat/completions", api_key_env="DIGEST_OPENAI_API_KEY", timeout=17, max_output_tokens=42, opener=opener)
            self.assertEqual(model.complete_structured("summarize", {"type": "object"}), '{"sections":[]}')

        self.assertEqual(captured["timeout"], 17)
        self.assertEqual(captured["payload"]["response_format"]["type"], "json_schema")
        self.assertEqual(captured["payload"]["max_tokens"], 42)
        self.assertNotIn("fixture-secret", json.dumps(captured["payload"]))
        self.assertIn("Bearer fixture-secret", captured["headers"]["Authorization"])
    def test_anthropic_adapter_uses_messages_api_and_extracts_text_block(self) -> None:
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"content":[{"type":"text","text":"{\\"rows\\":[]}"}]}'

        def opener(request, timeout):
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data)
            return Response()

        with patch.dict(os.environ, {"DIGEST_ANTHROPIC_API_KEY": "fixture-secret"}, clear=False):
            model = AnthropicMessagesModel("model-id", endpoint="https://api.anthropic.com/v1/messages", api_key_env="DIGEST_ANTHROPIC_API_KEY", timeout=17, max_output_tokens=42, opener=opener)
            self.assertEqual(model.complete_structured("classify", {"type": "object"}), '{"rows":[]}')

        self.assertEqual(captured["payload"]["max_tokens"], 42)
        self.assertIn("Return JSON matching this schema exactly", captured["payload"]["messages"][0]["content"])
        self.assertEqual(captured["headers"]["Anthropic-version"], "2023-06-01")


if __name__ == "__main__":
    unittest.main()
