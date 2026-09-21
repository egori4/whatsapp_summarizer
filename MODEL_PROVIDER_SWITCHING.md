# Model-provider switching (candidate artifact)

This document applies only to the isolated candidate worktree. It does **not** authorize a live-policy change, external inference, delivery, or service restart.

## Provider-neutral model policy

The `models` object chooses one provider for `preclassifier`, `final`, and `fallback` roles:

```json
{
  "provider": "ollama",
  "endpoint": "http://127.0.0.1:11434",
  "preclassifier": "qwen3.5:4b",
  "final": "qwen3.5:9b",
  "fallback": "qwen3.5:4b"
}
```

The runner now calls `build_model(models, role)` rather than directly constructing Ollama adapters. Supported providers are:

- `ollama` — loopback HTTP only; no API-key setting is permitted.
- `openai` — HTTPS Chat Completions-compatible endpoint; uses strict JSON-schema output.
- `anthropic` — HTTPS Messages endpoint; injects the same JSON schema into the request and relies on the existing fail-closed grounding validator.
- `hermes-openai-codex` — calls the local Hermes CLI, which uses its own stored `openai-codex` OAuth credential. Its policy endpoint is exactly `local://hermes-cli`; an API-key setting is forbidden.

Cloud credentials are never placed in policy. The direct HTTPS providers use only an environment-variable name ending in `_API_KEY`. The Hermes Codex provider never reads, copies, or serializes an OAuth credential.

## Hermes Codex policy shape

```json
{
  "provider": "hermes-openai-codex",
  "endpoint": "local://hermes-cli",
  "preclassifier": "gpt-5.6-terra",
  "final": "gpt-5.6-terra",
  "fallback": "gpt-5.6-terra"
}
```

The adapter invokes `hermes chat` in one-shot, safe-mode, no-rules mode with only Hermes’ empty `context_engine` toolset. The prompt travels through a temporary `0600` file (never a command-line argument) and is removed after every call. Model output is still passed through the existing schema/grounding validator. If a returned candidate fails that validator, the configured fallback receives exactly one fresh, validator-directed repair attempt with only the concise local failure reason—not the rejected output. Transport/provider failures use the ordinary fallback path without repair feedback when the fallback is genuinely distinct. When final and fallback resolve to the same policy model, they share one adapter and a timeout is terminal rather than launching an identical second call. A second invalid candidate fails closed and produces no digest.

For this provider, set `"reasoning_effort"` explicitly in policy. The adapter passes that value as `hermes chat --reasoning <value>`, so digest reasoning does not inherit the active Hermes profile's default. The verified replay policy uses `"reasoning_effort": "high"` with `gpt-5.6-terra` for both final and fallback roles.

### Hermes Codex budget contract

`hermes chat --help` exposes no output-token/max-output switch. Therefore the adapter never pretends that `max_output_tokens` constrains Hermes. Instead, `models.context_limit` is enforced as a UTF-8 byte limit on the complete prompt (including schema contract) before a `0600` temporary file is created or Hermes runs, and mandatory `models.max_output_chars` is enforced on Hermes stdout before JSON/schema parsing. Either overflow is a visible `ModelFailure`, never silent truncation. `max_output_tokens` remains the real request setting for Ollama and direct HTTPS adapters.

### One-pass raw-message mode

Set `"pipeline_mode": "one_pass"` only with `hermes-openai-codex` to skip the local preclassifier entirely. The runner normalizes/redacts the current durable message revisions, sends the complete resulting source window to the Hermes final model, and requires a disposition for every source. Hermes is instructed to group related sources internally into discussion threads, retain supporting content inside a relevant thread, and omit only whole irrelevant threads from the reader-facing sections. There is no timestamp-neighbor context expansion and no Ollama call in this mode.

The Conditional Phase C candidate uses `"pipeline_mode": "two_call"` with the same Hermes-only connector restriction and also skips the preclassifier. Its extractor sees the complete projected window; only locally validated exact evidence atoms and structural context reach reconciliation. It normally uses two application calls and permits one validation-only repair across the whole pipeline, never a transport retry. This source path remains unqualified and unselected until its independent review and repeated qualification gates pass.

## Example cloud-policy shape

```json
{
  "provider": "openai",
  "endpoint": "https://api.openai.com/v1/chat/completions",
  "api_key_env": "DIGEST_OPENAI_API_KEY",
  "preclassifier": "<classifier-model-id>",
  "final": "<final-model-id>",
  "fallback": "<fallback-model-id>"
}
```

For Anthropic, use `"provider": "anthropic"`, an HTTPS Messages endpoint, and an environment-variable name such as `DIGEST_ANTHROPIC_API_KEY`.

## Required safety checks before any future activation

1. Keep the production policy untouched until explicit artifact approval.
2. Create an owner-only backup and an isolated test spool.
3. Set only an environment-variable *name* in policy; inject the value through the approved secret-management path.
4. Run fixture-only contract tests and inspect representative input, structured response, and rendered digest.
5. Obtain separate explicit approval before an external inference test, then separately before any activation/restart or delivery.

The structured final contract and `render_grounded` validator remain provider-independent: source-verbatim claims, questions, answers/actions, and URLs must validate before a digest can advance.
