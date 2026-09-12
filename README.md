# WhatsApp Tech Digest

A safety-gated collector and technical-digest pipeline for one explicitly approved WhatsApp group. It is **not** a conversational WhatsApp bot.

## Current status

**Read [`HANDOFF.md`](HANDOFF.md) and [`AGENTS.md`](AGENTS.md) before making changes.** This public repository intentionally contains only sanitized source, tests, and examples; live target identity, runtime state, and operational evidence remain owner-only and Git-ignored.

The merged implementation provides a selective one-pass Hermes digest, compact renderer, and exact-source topic questions and conclusions. The published artifact is not a deployment: policy installation, service activation, scheduling, model execution, and delivery remain separate operator-controlled actions.

Before publishing or deploying any change:

- run the full test suite and `git diff --check`;
- confirm no policy, group directory, spool, rendered artifact, log, credential, session, recipient identity, or local path is staged;
- use only the sanitized nondeployable policy example in this repository.

## Version control

The project uses semantic versioning. The single source of truth is `version` in [`pyproject.toml`](pyproject.toml), mirrored by `whatsapp_tech_digest.__version__`; update both in the same commit.

| Version | Scope |
| --- | --- |
| `0.2.0` | Source-grounding and identifier-privacy hardening for the one-pass digest (current) |
| `0.1.0` | Collector, spool, two-stage and one-pass pipelines, reviewed-artifact delivery, inert scheduler source |

Versioning rules for this repository:

- **Major** — a change to the collector-only boundary, the outbound-deny guard, the policy contract, or the reviewed-artifact delivery gate.
- **Minor** — new or materially changed digest validation, grounding, rendering, privacy projection, or execution-mode behavior.
- **Patch** — bug fixes, test additions, and documentation that do not change validated output.

Only sanitized source, tests, examples, and documentation are committed. Policy, group directory, spool, rendered artifacts, review manifests, logs, credentials, and session data stay Git-ignored and owner-only. Run the full test suite and `git diff --check` before every commit, and obtain independent review for logic, privacy, or delivery-boundary changes.

## Current Version-Controlled Change

**`0.2.0` — Harden actionable digest grounding and unresolved tracking.**

Grounding:

- Substantive reader prose (question, narrative, recommendation, actions, limitation) must be a contiguous, word-bounded, normalized exact source excerpt after identity sanitization; fuzzy matching, cross-source stitching, and mid-word excerpts are rejected.
- An excerpt that drops an immediately preceding negator is rejected, so a prohibition cannot be rendered as an instruction.
- Protected technical identifiers use exact token occurrence instead of substring containment, covering short and punctuation-bearing forms such as `v2`, `3PAR`, `SRX_345`, `802.11ax`, and `C++17`.

Question and unresolved-issue classification:

- A reader-facing question requires a model-declared `QUESTION` kind plus an independent local check on **both** the cited source and the selected excerpt.
- A bare wh- opening now requires interrogative punctuation, so relative and exclamative clauses are not recast as questions.
- `ISSUE` entries require an unresolved technical problem signal and reject explicit resolution language unless the source states the problem is still open.
- Unanswered topic labels are internal metadata and are not rendered; reader status text is derived locally. Unanswered entries are capped at eight.

Privacy:

- One shared identifier sanitizer backs both the model projection and reader-facing output, covering numeric mentions with optional `+` and device suffixes, participant/group/web/`lid` JID forms, and newsletter/broadcast handles.

Other:

- An unanswered-only source window is valid; only a window with neither retained updates nor validated unanswered items is rejected.
- A `recommendation` is no longer dropped when the same topic also carries explicit actions.
- Generated systemd units use `%h` instead of an embedded home path.
- Restored the publication-safe [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md), [`RUNBOOK.md`](RUNBOOK.md), and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

Known limitation: an all-nonmaterial active window stays pending instead of advancing an empty checkpoint. This is fail-closed and is scheduled for the isolated quality-evaluation phase before unattended operation.

These are inert source artifacts. They do not install or enable units, access credentials, run a model, send email, or activate a schedule. Production policy, runtime state, credentials, rendered artifacts, and activation evidence remain owner-only and Git-ignored.

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
all eligible normalized current message revisions
  -> Hermes local CLI adapter
  -> openai-codex / gpt-5.6-terra / explicit high reasoning
  -> thread-level actionable schema
  -> strict local validation and rendering
```

Set `"pipeline_mode": "one_pass"` only with provider `hermes-openai-codex` and endpoint `local://hermes-cli`. The runner bypasses Ollama preclassification so supporting context in the eligible source window is not dropped. The model clusters that window, while the local renderer validates source references, word-bounded exact normalized and privacy-sanitized source excerpts, exact protected-token membership, attribution, confidence, and unresolved issues. Excerpts that drop an immediately preceding negator are rejected.

Redaction scope: normalization derives a `redacted_text` field that masks secret-shaped assignments, opaque numeric mentions including optional `+` and device suffixes, supported WhatsApp participant/group/web JID forms, and newsletter/broadcast handles. The same shared identifier sanitizer protects source text quoted by the local renderer. Model prompts use an explicit projection containing opaque per-request source refs, redacted text, timestamps, and resolvable opaque reply linkage only. Raw message IDs, participant/display metadata, chat identifiers, and runtime metadata remain local. One-pass mode deterministically excludes messages flagged `mechanical_ack` or `untrusted_policy_override` before constructing the model prompt.

The verified replay policy uses `reasoning_effort: high`, passed explicitly as `hermes chat --reasoning high`; it is not inherited from the active Hermes profile. Credentials remain inside Hermes' supported stored OAuth abstraction.

The one-pass actionable path is structurally isolated into `actionable_schema.py`, `actionable_validate.py`, and `actionable_render.py`. `models.py` retains compatibility entry points for the legacy two-stage pipeline; this source-only split does not change execution modes, model invocation, delivery, or runtime configuration.

### Explicit runner execution modes

The runner does not use an ambiguous `dry_run` switch. It accepts `--execution-mode` and defaults to the safest mode:

- `validate-only` (default): parses policy, snapshots the spool, checks omissions/current revisions, and performs deterministic normalization. It never builds a model, sends SMTP, records a digest run, or advances a checkpoint.
- `render-only`: performs the same integrity checks and allows model rendering, printing the rendered digest. It never sends SMTP, records a digest run, or advances a checkpoint. In one-pass Hermes Codex mode, only the allowlisted redacted source projection crosses the local Hermes CLI/provider boundary.
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

The final digest should be a selective senior-engineer brief, not a transcript. It renders no raw-message audit section and hides empty/non-applicable fields. A retained Q&A topic begins with an exact normalized, privacy-sanitized source excerpt in `Question asked`; generated titles organize the brief, while substantive questions, summaries, recommendations, actions, and limitations must each be a contiguous source excerpt. Announcements and directives render directly as updates and actions. Retain only reusable material such as:

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
