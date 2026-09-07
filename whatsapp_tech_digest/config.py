from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


class ConfigError(ValueError):
    """A schema-v1 policy is incomplete, unsafe, or outside the approved scope."""


_JID = re.compile(r"^[0-9]{8,}(?:-[0-9]{8,})?@g\.us$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+$")


def _object(value: object, field: str, allowed: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{field} contains unknown keys: {', '.join(sorted(unknown))}")
    return value


def _exact_keys(data: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"policy contains unknown keys: {', '.join(sorted(unknown))}")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigError(f"{field} must be a nonempty trimmed string")
    return value


def _positive(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{field} must be a positive integer")
    return value


def _artifact_digest(value: object, field: str, provider: str) -> str | None:
    """Require local artifact identities only for the local model provider."""
    if value is None and provider != "ollama":
        return None
    return _text(value, field)


@dataclass(frozen=True)
class SilencePolicy:
    collector_consume: bool
    bridge_deny_all_post: bool
    operation_scope: tuple[str, ...]


@dataclass(frozen=True)
class ModelPolicy:
    provider: str
    pipeline_mode: str
    preclassifier: str
    preclassifier_digest: str | None
    final: str
    final_digest: str | None
    fallback: str
    fallback_digest: str | None
    timeout_seconds: int
    batch_size: int
    context_limit: int
    noise_threshold: float
    endpoint: str
    api_key_env: str | None
    reasoning_effort: str
    max_output_tokens: int
    max_output_chars: int
    classifier_instruction: str
    final_instruction: str


@dataclass(frozen=True)
class ExternalFallbackPolicy:
    enabled: bool
    approved: bool
    reduced_bundle_only: bool


@dataclass(frozen=True)
class SmtpPolicy:
    host: str
    port: int
    sender: str
    recipient: str
    password_env: str
    timeout_seconds: int


@dataclass(frozen=True)
class DigestConfig:
    schema_version: int
    example_only: bool
    target_group_jid: str
    silence: SilencePolicy
    whatsapp: Mapping[str, Any]
    schedule: Mapping[str, Any]
    retention: Mapping[str, int]
    paths: Mapping[str, str]
    runtime: Mapping[str, int]
    health: Mapping[str, int]
    contacts: Mapping[str, str]
    redaction: Mapping[str, Any]
    models: ModelPolicy
    external_fallback: ExternalFallbackPolicy
    smtp: SmtpPolicy

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DigestConfig":
        _exact_keys(data, {"schema_version", "example_only", "target_group_jid", "silence", "whatsapp", "schedule", "retention", "paths", "runtime", "health", "contacts", "redaction", "models", "external_fallback", "smtp"})
        if data.get("schema_version") != 1:
            raise ConfigError("only schema_version 1 is supported")
        example_only = data.get("example_only") is True
        jid = _text(data.get("target_group_jid"), "target_group_jid")
        if not _JID.fullmatch(jid):
            raise ConfigError("target_group_jid must be an exact immutable @g.us JID")

        silence_data = _object(data.get("silence"), "silence", {"collector_consume", "bridge_deny_all_post", "operation_scope"})
        operations = silence_data.get("operation_scope")
        required_operations = {"send", "edit", "media", "poll", "location", "typing", "read", "progress", "unknown"}
        if not isinstance(operations, list) or set(operations) != required_operations or len(operations) != len(required_operations):
            raise ConfigError("silence.operation_scope must enumerate every immutable output primitive")
        silence = SilencePolicy(silence_data.get("collector_consume") is True, silence_data.get("bridge_deny_all_post") is True, tuple(sorted(operations)))
        if not (silence.collector_consume and silence.bridge_deny_all_post):
            raise ConfigError("both independent silence controls must be true")

        whatsapp = _object(data.get("whatsapp"), "whatsapp", {"mode", "group_policy", "group_allow_from", "require_mention", "unauthorized_dm", "send_read_receipts", "bridge_port"})
        if whatsapp["mode"] != "allowlist" or whatsapp["group_policy"] != "allowlist" or whatsapp["group_allow_from"] != [jid] or whatsapp["require_mention"] is not False or whatsapp["unauthorized_dm"] != "ignore" or whatsapp["send_read_receipts"] is not False or not isinstance(whatsapp["bridge_port"], int) or not 0 <= whatsapp["bridge_port"] <= 65535:
            raise ConfigError("whatsapp policy must be an exact sole-JID allowlist/ignore/no-read-receipt policy")

        schedule = _object(data.get("schedule"), "schedule", {"timezone", "expression", "activate_only_after_dst_contract"})
        if schedule != {"timezone": "America/Toronto", "expression": "0 8 * * *", "activate_only_after_dst_contract": True}:
            raise ConfigError("schedule must be 0 8 * * * America/Toronto with DST activation gate")
        retention = _object(data.get("retention"), "retention", {"raw_days", "digest_days"})
        if retention != {"raw_days": 7, "digest_days": 90}:
            raise ConfigError("retention must be raw_days=7 and digest_days=90")
        paths = _object(data.get("paths"), "paths", {"spool", "state_dir", "mode"})
        if paths.get("mode") != "0700" or not _text(paths.get("spool"), "paths.spool") or not _text(paths.get("state_dir"), "paths.state_dir"):
            raise ConfigError("paths must name state locations with owner-only mode 0700")
        runtime = _object(data.get("runtime"), "runtime", {"lock_seconds", "max_runtime_seconds"})
        runtime = {key: _positive(runtime.get(key), f"runtime.{key}") for key in runtime}
        health = _object(data.get("health"), "health", {"heartbeat_seconds"})
        health = {"heartbeat_seconds": _positive(health.get("heartbeat_seconds"), "health.heartbeat_seconds")}
        contacts = _object(data.get("contacts"), "contacts", {"fallback"})
        if contacts.get("fallback") not in {"display_name", "participant"}:
            raise ConfigError("contacts.fallback must be display_name or participant")
        redaction = _object(data.get("redaction"), "redaction", {"enabled"})
        if redaction.get("enabled") is not True:
            raise ConfigError("redaction.enabled must be true")

        model_data = _object(data.get("models"), "models", {"provider", "pipeline_mode", "preclassifier", "preclassifier_digest", "final", "final_digest", "fallback", "fallback_digest", "timeout_seconds", "batch_size", "context_limit", "noise_threshold", "endpoint", "api_key_env", "reasoning_effort", "max_output_tokens", "max_output_chars", "classifier_instruction", "final_instruction"})
        provider = model_data.get("provider", "ollama")
        pipeline_mode = model_data.get("pipeline_mode", "two_stage")
        if pipeline_mode not in {"two_stage", "one_pass"}:
            raise ConfigError("models.pipeline_mode must be two_stage or one_pass")
        if provider not in {"ollama", "openai", "anthropic", "hermes-openai-codex"}:
            raise ConfigError("models.provider must be ollama, openai, anthropic, or hermes-openai-codex")
        api_key_env = model_data.get("api_key_env")
        reasoning_effort = model_data.get("reasoning_effort", "medium")
        if reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ConfigError("models.reasoning_effort must be an explicit supported effort, not inherited")
        if provider in {"ollama", "hermes-openai-codex"}:
            if api_key_env is not None:
                raise ConfigError("models.api_key_env must be omitted for local providers")
        elif not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*_API_KEY", api_key_env):
            raise ConfigError("cloud model providers require an API-key environment variable name")
        models = ModelPolicy(
            provider, pipeline_mode, _text(model_data.get("preclassifier"), "models.preclassifier"), _artifact_digest(model_data.get("preclassifier_digest"), "models.preclassifier_digest", provider),
            _text(model_data.get("final"), "models.final"), _artifact_digest(model_data.get("final_digest"), "models.final_digest", provider),
            _text(model_data.get("fallback"), "models.fallback"), _artifact_digest(model_data.get("fallback_digest"), "models.fallback_digest", provider),
            _positive(model_data.get("timeout_seconds"), "models.timeout_seconds"), _positive(model_data.get("batch_size"), "models.batch_size"),
            _positive(model_data.get("context_limit"), "models.context_limit"), float(model_data.get("noise_threshold", -1)), _text(model_data.get("endpoint"), "models.endpoint"), api_key_env, reasoning_effort, _positive(model_data.get("max_output_tokens"), "models.max_output_tokens"), _positive(model_data.get("max_output_chars"), "models.max_output_chars"), _text(model_data.get("classifier_instruction"), "models.classifier_instruction"), _text(model_data.get("final_instruction"), "models.final_instruction"),
        )
        if not 0 <= models.noise_threshold <= 1:
            raise ConfigError("models.noise_threshold must be between 0 and 1")
        if models.provider == "ollama" and (models.preclassifier != "qwen3.5:4b" or models.final != "qwen3.5:9b" or models.fallback != "qwen3.5:4b"):
            raise ConfigError("local ollama must use qwen3.5 4b/9b/4b")
        if models.provider == "ollama" and not models.endpoint.startswith("http://127.0.0.1:"):
            raise ConfigError("local ollama requires an explicit loopback endpoint")
        if models.provider == "openai" and not models.endpoint.startswith("https://"):
            raise ConfigError("openai provider requires an HTTPS endpoint")
        if models.provider == "anthropic" and not models.endpoint.startswith("https://"):
            raise ConfigError("anthropic provider requires an HTTPS endpoint")
        if models.provider == "hermes-openai-codex" and models.endpoint != "local://hermes-cli":
            raise ConfigError("hermes-openai-codex requires the exact local://hermes-cli connector endpoint")
        if models.pipeline_mode == "one_pass" and models.provider != "hermes-openai-codex":
            raise ConfigError("one_pass mode is restricted to the Hermes Codex provider")
        external_data = _object(data.get("external_fallback"), "external_fallback", {"enabled", "approved", "reduced_bundle_only"})
        external = ExternalFallbackPolicy(external_data.get("enabled") is True, external_data.get("approved") is True, external_data.get("reduced_bundle_only") is True)
        if external.enabled and not (external.approved and external.reduced_bundle_only):
            raise ConfigError("external fallback requires explicit approval and reduced-bundle-only boundary")
        if external.enabled:
            raise ConfigError("external fallback is not implemented or approved in this offline artifact")

        smtp_data = _object(data.get("smtp"), "smtp", {"host", "port", "sender", "recipient", "password_env", "timeout_seconds"})
        smtp = SmtpPolicy(_text(smtp_data.get("host"), "smtp.host"), _positive(smtp_data.get("port"), "smtp.port"), _text(smtp_data.get("sender"), "smtp.sender"), _text(smtp_data.get("recipient"), "smtp.recipient"), _text(smtp_data.get("password_env"), "smtp.password_env"), _positive(smtp_data.get("timeout_seconds"), "smtp.timeout_seconds"))
        if not (_EMAIL.fullmatch(smtp.sender) and _EMAIL.fullmatch(smtp.recipient)) or smtp.port != 587 or not smtp.password_env.endswith("_PASSWORD"):
            raise ConfigError("SMTP must use valid fixed-policy sender/recipient addresses, STARTTLS port 587, and a password reference")
        return cls(1, example_only, jid, silence, dict(whatsapp), dict(schedule), dict(retention), dict(paths), runtime, health, dict(contacts), dict(redaction), models, external, smtp)

    def schedule_activation_allowed(self, observed_local_times: Mapping[str, str]) -> bool:
        return observed_local_times.get("spring") == "08:00" and observed_local_times.get("fall") == "08:00"

    def require_live_safe(self) -> None:
        if self.example_only:
            raise ConfigError("sanitized example policy is deliberately not deployable")
        if not 1024 <= int(self.whatsapp["bridge_port"]) <= 65535 or self.whatsapp["bridge_port"] == 3000:
            raise ConfigError("live policy requires a dedicated nonzero loopback bridge port, not 3000")
        if self.models.provider == "ollama" and not self.models.endpoint.startswith("http://127.0.0.1:"):
            raise ConfigError("local live model endpoint must be an explicit loopback endpoint")
        if self.models.provider in {"openai", "anthropic"} and (not self.models.endpoint.startswith("https://") or not self.models.api_key_env):
            raise ConfigError("cloud live model requires HTTPS and an API-key environment variable name")
        if self.models.provider == "hermes-openai-codex" and (self.models.endpoint != "local://hermes-cli" or self.models.api_key_env is not None):
            raise ConfigError("Hermes Codex live model requires the exact local connector with no API-key reference")
        if self.models.provider == "ollama" and any(not re.fullmatch(r"sha256:[0-9a-f]{64}", digest or "") for digest in (self.models.preclassifier_digest, self.models.final_digest, self.models.fallback_digest)):
            raise ConfigError("live local-Ollama policy requires verified non-placeholder model digests")
        if self.models.provider == "hermes-openai-codex" and (self.models.final, self.models.fallback) != ("gpt-5.6-terra", "gpt-5.6-terra"):
            raise ConfigError("live Hermes Codex policy must use the approved gpt-5.6-terra final and fallback model identifiers")
        if self.smtp.host.endswith(".example.invalid") or self.smtp.sender.endswith("@example.invalid"):
            raise ConfigError("live policy cannot use SMTP examples/placeholders")
        if any(not path.startswith("/") or "/../" in path for path in (self.paths["spool"], self.paths["state_dir"])):
            raise ConfigError("live policy paths must be absolute and traversal-free")