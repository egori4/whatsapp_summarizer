# WhatsApp Tech Digest

A safety-gated collector and technical-digest pipeline for one explicitly approved WhatsApp group. It is **not** a conversational WhatsApp bot.

## Current status

**Read [`HANDOFF.md`](HANDOFF.md) and [`AGENTS.md`](AGENTS.md) before making changes.** This public repository intentionally contains only sanitized source, tests, and examples; live target identity, runtime state, and operational evidence remain owner-only and Git-ignored.

The merged implementation provides a selective one-pass Hermes digest, compact renderer, and concise source-grounded topic questions. The published artifact is not a deployment: policy installation, service activation, scheduling, model execution, and delivery remain separate operator-controlled actions.

Before publishing or deploying any change:

- run the full test suite and `git diff --check`;
- confirm no policy, group directory, spool, rendered artifact, log, credential, session, recipient identity, or local path is staged;
- use only the sanitized nondeployable policy example in this repository.

## Data flow

```text
Approved WhatsApp group (exact immutable JID)
  -> loopback Baileys bridge
  -> fail-closed collector hook and outbound POST guard
  -> durable local SQLite spool
  -> deterministic normalization/redaction
  -> selected digest pipeline
  -> local rendered artifact
  -> fixed-recipient SMTP (future; separate approval)
```

### Supported digest modes

#### Two-stage local mode

```text
normalized messages
  -> loopback Ollama qwen3.5:4b high-recall selection
  -> qwen3.5:9b grounded digest
  -> qwen3.5:4b fallback
```

This remains the default for policies that omit `models.pipeline_mode`.

#### One-pass Hermes Codex mode

```text
all normalized current message revisions
  -> Hermes local CLI adapter
  -> openai-codex / gpt-5.6-terra / explicit high reasoning
  -> thread-level actionable schema
  -> strict local validation and rendering
```

Set `"pipeline_mode": "one_pass"` only with provider `hermes-openai-codex` and endpoint `local://hermes-cli`. The runner bypasses Ollama preclassification so supporting thread context is not dropped. The model clusters complete conversations, while the local renderer validates source references, commands, versions, identifiers, URLs, attribution, confidence, and unresolved issues.

Redaction scope: normalization derives a `redacted_text` field that masks secret-shaped `key/token/password` assignments, and every reader-facing grounding check validates against that redacted form, so a masked secret cannot reach the rendered digest. The prompt itself is built from the complete normalized source record, so raw message text, participant identifiers, and display names do cross the configured model boundary. One-pass mode also forwards messages flagged `mechanical_ack` and `untrusted_policy_override` to the model; unlike two-stage mode, they are not dropped before the prompt, and injection resistance rests on the untrusted-data instruction plus the local output validator.

The verified replay policy uses `reasoning_effort: high`, passed explicitly as `hermes chat --reasoning high`; it is not inherited from the active Hermes profile. Credentials remain inside Hermes' supported stored OAuth abstraction.

The one-pass actionable path is structurally isolated into `actionable_schema.py`, `actionable_validate.py`, and `actionable_render.py`. `models.py` retains compatibility entry points for the legacy two-stage pipeline; this source-only split does not change execution modes, model invocation, delivery, or runtime configuration.

### Explicit runner execution modes

The runner does not use an ambiguous `dry_run` switch. It accepts `--execution-mode` and defaults to the safest mode:

- `validate-only` (default): parses policy, snapshots the spool, checks omissions/current revisions, and performs deterministic normalization. It never builds a model, sends SMTP, records a digest run, or advances a checkpoint.
- `render-only`: performs the same integrity checks and allows local model rendering, printing the rendered digest. It never sends SMTP, records a digest run, or advances a checkpoint. In one-pass Hermes Codex mode, the complete normalized source records — including raw text and participant metadata — cross the local Hermes CLI/provider boundary in this mode.
- `delivery-capable`: is the only mode permitted to call SMTP or persist digest-run/checkpoint outcomes. It invokes `require_live_safe()` and remains subject to the separate live-policy and operational-approval gates; selecting it in source does not authorize activation or delivery.

Read-only state commands (`--health`, `--status`, `--purge`, and reconciliation commands) do not accept a non-default execution mode.

### Reviewed-artifact delivery boundary

A render-only run may be given `--review-manifest <owner-only.json>`. After deterministic output validation, it records a revision-only review envelope in the spool and writes an owner-only manifest containing only an opaque review ID and the rendered-content hash—never source text, rendered prose, recipients, or credentials. This does not create a digest run/provenance record, contact SMTP, or advance the checkpoint.

A later `--execution-mode delivery-capable --review-manifest <owner-only.json> --deliver-reviewed-artifact <reviewed.md>` validates the exact artifact hash, policy hash, immutable current source revisions, and checkpoint before SMTP. It sends the reviewed bytes directly and **does not rerun a model**. A changed artifact, changed policy, revoked/superseded source, or changed checkpoint blocks SMTP. The command remains separately approval-gated; selecting it does not authorize delivery.

### Hermes Codex size budgets

The local Hermes CLI exposes no model-output cap flag (verified with `hermes chat --help`), so its limits are enforced in the adapter rather than implied by a nonexistent CLI option:

- `models.context_limit` is a mandatory UTF-8 byte ceiling for the entire Hermes prompt, including the structured-output contract. An oversized prompt fails before a temporary prompt file is created or Hermes is invoked.
- `models.max_output_chars` is a mandatory character ceiling on Hermes stdout, checked before any JSON/schema parsing or repair attempt. An oversized candidate fails closed; it is never truncated.
- `models.max_output_tokens` remains the actual provider request limit for Ollama and direct HTTPS providers. It is not claimed as a Hermes CLI control.

### Declarative-only policy fields

The complete field-use matrix and schema-v1 compatibility decision are in [`POLICY_CONTRACT.md`](POLICY_CONTRACT.md). Some schema fields are validated as a deployment contract but are not read by the runtime, and the documentation does not claim otherwise:

- `retention` must be exactly `raw_days=7`/`digest_days=90`; `DurableSpool.purge()` applies those same values as code constants rather than reading the policy.
- `contacts.fallback` is validated, but attribution always prefers `display_name` and falls back to `participant` regardless of the setting.
- `redaction.enabled` must be `true` and cannot be turned off; it selects no alternative behavior.
- `runtime.lock_seconds`, `runtime.max_runtime_seconds`, and `health.heartbeat_seconds` are validated as positive integers only. Run exclusivity comes from the `--lock` flock and there is no in-process runtime cap.
- `models.context_limit` bounds the prompt only for `hermes-openai-codex`. The Ollama and direct HTTPS adapters apply no prompt-size ceiling.

## Reader-facing summarization goal

The final digest should be a selective senior-engineer brief, not a transcript. It renders no raw-message audit section and hides empty/non-applicable fields. Each retained topic begins with a concise source-grounded `Question asked` line, then the situation/conclusion. Retain only reusable material such as:

- concrete upgrade/version guidance and failure signatures;
- operational commands and procedures;
- migration tools and explicit scope limitations;
- troubleshooting conclusions and superseded-advice corrections;
- useful repositories, KBs, and documentation URLs;
- materially important unresolved questions with asker attribution;
- field guidance clearly distinguished from confirmed documentation.

It is better to omit minor discussion than flood the digest with low-value chatter.

## Native collector plugin

`hermes_plugin/whatsapp_tech_digest_collector/` is the deployable native Hermes plugin package installed under `~/.hermes/plugins/`. It has no tool, model, SMTP, scheduler, or platform-action capability. For the exact policy-pinned group, it converts the minimal inbound bridge event to a durable spool record and returns `{"action":"skip"}`. Target-event record conversion, spool initialization, or spool-write failures are caught locally and return the generic fail-closed `{"action":"skip","reason":"whatsapp_tech_digest_collection_failed"}` directive; no event content, participant, message ID, or JID is emitted in failure telemetry. Each target hook invocation owns and closes its SQLite handle; appends use a bounded 250 ms SQLite busy timeout plus one retry for a transient lock, while exhausted failures still return the same generic fail-closed directive. Non-target events preserve normal dispatch.

## Safety invariants

1. **One exact group identifier.** Never use display-name matching, aliases, wildcards, or bare numbers.
2. **Collector-only behavior.** Group content must never become a normal Hermes agent turn.
3. **No WhatsApp output.** Deny send, edit, media, poll, location, typing, read-receipt, progress, and unknown output routes.
4. **Loopback services.** Keep the bridge at `127.0.0.1:17778` and Ollama at `127.0.0.1:11434`.
5. **Private artifacts.** Policies, exports, databases, logs, backups, prompts, and digests are owner-only and ignored.
6. **No silent delivery.** SMTP requires a fixed recipient, protected credential reference, acceptance reconciliation, and separate approval.
7. **No automated schedule yet.** Scheduling/activation remains separately gated.
8. **Fail closed.** Source-coverage, revocation, schema, grounding, model, or delivery uncertainty blocks checkpoint advancement.
9. **Separate approvals.** Artifact acceptance does not authorize merge, deployment, restart, target restoration, inference activation, SMTP, scheduling, or cleanup.
10. **Revision-only provenance.** A validated one-pass digest that reaches accepted or delivery-unknown state stores a hash-bound, owner-only topic-to-immutable-revision envelope. It contains no message text, participant display name, reader-facing prose, URL, or model rationale; failed candidates never receive trusted provenance.

## Documentation map

- `AGENTS.md` — mandatory durable rules for any coding model.
- `HANDOFF.md` — current development and live-runtime facts, evidence, gaps, and next approval gates.
- `MODEL_PROVIDER_SWITCHING.md` — provider, one-pass, explicit reasoning, and credential-boundary behavior.
- `ARCHITECTURE_THREAT_MODEL.md` — components, trust boundaries, assets, and controls.
- `DEPLOYMENT_GUIDE.md` — staged deployment and rollback boundaries.
- `RUNBOOK.md` — operational verification and incident procedures.
- `IMPLEMENTATION_PLAN.md` — historical implementation/evidence map.
- `DAILY_SCHEDULER_DESIGN.md` — staged production cadence and reviewed-delivery design; no timer is enabled.
- `config/digest.policy.example.json` — sanitized nondeployable schema example; real policies are ignored.

## Safe candidate verification

```bash
cd "$REPLAY_WORKTREE"
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q
git diff --check
git status --short
```

These commands do not send WhatsApp messages, invoke model inference, send SMTP, change the gateway, or activate scheduling. Read `HANDOFF.md` before any replay or state-changing operation.
