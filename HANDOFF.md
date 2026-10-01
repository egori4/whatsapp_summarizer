# WhatsApp Technical Digest — Engineering Handoff

**Last updated:** 2026-09-29  
**Host used for production/pilot:** `m920q`  
**Repository:** `egori4/whatsapp_summarizer`  
**Current working branch:** `v2.2-ollama-structured-output`
**Current merged baseline:** v2.1 on `main` at `549e989`
**Current package version on feature branch:** `2.2.0`

## 1. What this project does

The project collects messages from configured WhatsApp groups through a dedicated standalone Hermes/Baileys bridge, stores them in SQLite, periodically summarizes only new material with one model call, validates the model output, renders an email digest, sends it through SMTP, and advances a per-workflow checkpoint only after successful delivery. Hermes remains the default model gateway; v2.2 adds one explicit native Ollama structured-output path for `provider: ollama-local`.

The current implementation is intentionally narrow and operationally conservative. It is not a chatbot, does not reply to WhatsApp, and does not give the model tools or normal Hermes memory/context.

## 2. Authoritative documentation

Read these in this order:

1. `README.md` — deployment, configuration, operations, troubleshooting, model switching, upgrades.
2. `V2_ARCHITECTURE_SPEC.md` — original v2 architecture and invariants.
3. `V2_1_HISTORICAL_CONTEXT_PLAN.md` — v2.1 historical-context design and config-upgrade behavior.
4. `V2_2_OLLAMA_STRUCTURED_OUTPUT_ARCHITECTURE.md` — v2.2 native Ollama structured-output design, implementation, tests, live validation, and quality findings.
5. `HANDOFF.md` — current state, decisions, observed quality, and important caveats that span the above documents.

The README is the primary operations guide. When older architecture examples conflict with the current README/runtime, prefer the README and current code.

## 3. Current state

### v2.0 baseline

`main` was finalized and tagged `v2.0.0` at:

```text
8082c9a chore: remove obsolete v1 implementation and documentation
```

The v2.0 baseline had 98 passing v2 tests before v2.1 work began.

### v2.1 merged baseline

v2.1 was merged to `main` in PR #1 at merge commit:

```text
549e989 Merge pull request #1 from egori4/v2.1-historical-context
```

It includes historical context lookback, backward-compatible config upgrade behavior, updated architecture/handoff documentation, and local/custom Hermes provider model-switching guidance.

### v2.2 feature branch

Current work is isolated on:

```text
v2.2-ollama-structured-output
```

v2.2 adds only an explicit native Ollama structured-output path for `provider: ollama-local`. All other providers continue through the existing Hermes path.

Implemented behavior:

- direct native Ollama `/api/chat`;
- workflow-derived JSON Schema;
- `stream:false`, `think:false`, `temperature:0`;
- no cloud fallback;
- no repair/retry call;
- no lexical/keyword message classification;
- existing semantic/source validator retained;
- existing historical-context, freshness, delivery and checkpoint semantics retained.

Final local automated validation: `143 passed`, with `git diff --check` clean.

Live `qwen3.5:9b` structured-output testing passed application validation on acknowledgement/context, focused technical, and multi-topic cases. Model-quality caveats remain: duplicate topic placement across sections and answered questions sometimes remain listed as unresolved. These are documented in the v2.2 architecture file and are intentionally not hidden with heuristics or retry machinery.

## 4. Core architecture and invariants

```text
WhatsApp
   |
   v
whatsapp-digest-bridge.service
  127.0.0.1:3001
  configured groups only
   |
   v
whatsapp-digest-collector.service
   |
   v
SQLite
   |
   v
per-workflow systemd timer
   |
   v
one model call
   |-- existing Hermes gateway for non-Ollama providers
   `-- native Ollama structured output for explicit ollama-local
   |
   v
validate -> render -> SMTP
   |
   v
checkpoint
```

Do not violate these invariants without an explicit architecture decision:

- Hermes core remains unmodified.
- The normal Hermes gateway must not simultaneously own WhatsApp; only the dedicated digest bridge should consume that WhatsApp session.
- The digest has no WhatsApp outbound/reply path.
- WhatsApp text is untrusted source data, never instructions.
- Model invocation is one-shot, tool-free, memory-free, context-file-free, and has no automatic fallback.
- Recipient addresses and schedules come only from trusted configuration.
- Model output is schema/source validated before delivery.
- A failed model, validation, freshness, rendering, or SMTP step must not advance the checkpoint.
- Dry-runs never deliver and never advance the checkpoint.
- Pending messages are never purged by retention.

## 5. Checkpoint semantics

For a normal initialized run:

```text
cutoff_seq = highest current change_seq at run start
current rows = checkpoint_seq < change_seq <= cutoff_seq
```

Messages arriving after the cutoff stay pending for the next run.

The checkpoint advances only after successful real delivery. A first real run for a new workflow requires an explicit `--last` or `--since`; scheduled runs must never invent an initial historical window.

`--last` and `--since` only operate on messages already collected into local SQLite. They do not fetch historical WhatsApp data on demand.

## 6. v2.1 historical context

v2.1 adds read-only prior messages so a new digest can understand a conversation that spans digest boundaries.

Global defaults:

```yaml
context:
  enabled: true
  lookback: 48h
  max_messages: 100
```

Key rules:

- Applies to normal initialized checkpoint-driven runs.
- Historical rows must already be processed (`change_seq <= checkpoint_seq`).
- Same WhatsApp group only.
- Context is capped to the newest configured messages, then supplied chronologically.
- Explicit `--last` / `--since` runs do not add extra checkpoint history.
- Context alone never causes a model call.
- Context does not affect `Messages processed`, current window, cutoff, initialization, or checkpoint movement.
- Every output item must cite at least one **current** source ID.
- Historical IDs may additionally support a current item.
- If the prompt is too large, oldest context is trimmed first; current messages are never discarded merely to preserve history.
- Freshness validation includes the historical records actually supplied to the model.
- Raw QA output separates `HISTORICAL CONTEXT` from `CURRENT MESSAGES`.

This is deliberately not an AI-memory subsystem: no previous digest memory, vector database, embeddings, topic state, semantic retrieval, or second model call.

## 7. Real v2.1 quality evidence

A live scheduled digest was received on 2026-09-29 and is the strongest current quality observation.

Run:

```text
Run ID: 20260929T120001Z-global-ps-69577b00
Current messages: 35
Historical context messages: 2
Context lookback: 48h
Provider: openai-codex
Model: gpt-6-sol
Reasoning: medium
```

It correctly reduced the 35 current WhatsApp messages into three resolved technical updates and three unresolved technical questions. It retained key version numbers, licensing details, the Cyber Controller hardware-profile path/reboot/supportability warning, and correctly left unanswered questions unanswered rather than inventing answers.

The historical messages did not leak into the digest as a duplicate old update. This validates the anti-repetition boundary.

Important limitation of this observation: the run did **not** yet prove the strongest cross-checkpoint use case because the relevant question and short `Yes!` answer for the 5400S topic were both in the current window. A future quality observation should specifically look for a case where the question is historical and the current message is only a short continuation/reply.

Do not tune the prompt after every single digest. Collect several real daily runs and look for recurring failure patterns before changing summarization behavior.

Watch for:

- materially important thread omitted;
- unresolved question incorrectly treated as resolved;
- old context repeated as a new update;
- short cross-checkpoint reply misunderstood;
- unsupported technical conclusion;
- useful version/command/path/workaround lost;
- low-value chatter included too often.

## 8. Current model configuration and local Ollama considerations

At the time of this handoff, the live `global-ps` workflow on m920q is configured for:

```yaml
model:
  provider: ollama-local
  name: qwen3.5:9b
  reasoning: inherit
```

The `tests` workflow remains inherited from Hermes.

The Hermes configuration on m920q contains a named custom provider similar to:

```yaml
providers:
  ollama-local:
    api: http://127.0.0.1:11434/v1
    transport: chat_completions
    models:
      - qwen3.5:9b
      - qwen3.5:4b
```

and aliases similar to:

```yaml
model_aliases:
  local-qwen9b:
    model: qwen3.5:9b
    provider: ollama-local
  local-qwen4b:
    model: qwen3.5:4b
    provider: ollama-local
```

### Critical model-selection gotcha

The digest worker currently does **not** resolve Hermes `model_aliases`.

For the digest, use the concrete named provider and concrete model:

```yaml
provider: ollama-local
name: qwen3.5:9b
```

Do **not** use:

```yaml
provider: custom
name: local-qwen9b
```

Bare `custom` does not identify the named provider endpoint and caused:

```text
provider 'custom' resolved without credentials
```

Interactive Hermes/Telegram `/model local-qwen9b` may work because the interactive path resolves aliases; that does not mean the digest path will.

The source of truth for installed Hermes provider definitions is normally:

```bash
~/.hermes/config.yaml
```

For another local provider/model:

1. confirm the model exists (`ollama list` for Ollama);
2. inspect `providers:` in `~/.hermes/config.yaml`;
3. use that provider key plus the real model name in digest `config.yaml`;
4. run config validation;
5. run a small dry-run before a large window.

Example:

```bash
digest --config ./config.yaml config validate
digest --config ./config.yaml run global-ps --last 2h --dry-run
```

### Quality status of the local model

The strong 2026-09-29 digest quality observation above was generated by `gpt-6-sol`, **not** by the local Qwen model. Local `qwen3.5:9b` is currently a quality/performance experiment and should not yet be assumed equivalent. Observe both output quality and CPU/runtime on m920q before making it the long-term production choice.

## 9. Config schema upgrade mechanism

New optional config settings must remain backward-compatible at runtime. Normal application startup must never silently rewrite production YAML.

Current workflow:

```bash
digest --config ./config.yaml config validate
digest --config ./config.yaml config upgrade
digest --config ./config.yaml config upgrade --apply
```

Behavior:

- `config validate` remains valid for older configs and reports missing optional defaults.
- `config upgrade` creates a reviewable `config.yaml.upgraded` and does not touch active config.
- Merge behavior is recursive **add-missing-only**.
- Existing user values always win.
- `--apply` validates the generated candidate first, creates a backup (`.bak`, `.bak.1`, ...), then replaces the active file.
- Re-running after all current defaults are present is a no-op.
- PyYAML may normalize comments/formatting in the upgraded file even though values are preserved.

Current optional defaults are centralized in code so future optional blocks can use the same mechanism rather than adding one-off migration logic.

## 10. Editable install / branch switching

The project uses:

```bash
pip install -e .
```

This installs the repository in editable mode, so Python imports point at the current working tree. A branch switch therefore changes the code used by the next `digest` process without copying the package again.

Still run `pip install -e .` after a branch switch or pull when package metadata, version, dependencies, entry points, or package layout may have changed. It is cheap and removes ambiguity.

Recommended branch switch/update validation:

```bash
cd ~/scripts/whatsapp-tech-digest
git switch <branch>
git pull
source .venv/bin/activate
pip install -e .
pytest -q tests/test_v2_*.py
digest --config ./config.yaml config validate
```

If validation reports new optional defaults, use `config upgrade` for review.

Do not automatically reinstall transport and schedules after every pull.

## 11. When services must be reinstalled/restarted

Use the smallest operational action that matches the change:

| Change | Action |
| --- | --- |
| Runner/model/validation/render Python only | No service reinstall; next run uses new code |
| Python dependencies/package metadata | `pip install -e .` |
| Instructions/model/recipients/context/SMTP config | validate config; future runs read new values |
| `digest.env` secret | edit file, keep mode `600` |
| Workflow schedule | `digest ... schedule install` |
| Workflow/group JID add/remove/change | `digest ... transport install` + `digest ... schedule install` |
| Transport/generated transport-unit logic | `digest ... transport install` |
| Scheduler/generated unit logic | `digest ... schedule install` |
| Recreated virtualenv | `pip install -e .` |

Switching the checkout on m920q effectively changes what the next scheduled digest uses, because the scheduler launches the CLI from this editable checkout.

## 12. Test/acceptance state

Before v2.1 work:

```text
98 passed
```

After historical context and config-upgrade work:

```text
111 passed
```

A real v2.1 dry-run on m920q previously confirmed:

- 3 current messages;
- 2 historical-context messages;
- correct 5400S 35.0.2.0 synthesis;
- no delivery;
- no checkpoint movement;
- raw QA separation of history/current.

A subsequent real scheduled digest on 2026-09-29 provided successful production-quality evidence as described above.

Pre-merge checks should include:

```bash
pytest -q tests/test_v2_*.py
git diff --check
git status --short
```

and at least one real dry-run with the intended production model/provider.

## 13. Current operational configuration notes

At handoff time on m920q:

- `context.enabled: true`
- `context.lookback: 48h`
- `context.max_messages: 100`
- workflows present: `global-ps`, `tests`
- `global-ps` schedule: daily `07:30`
- `global-ps` model: `ollama-local / qwen3.5:9b / inherit`
- `tests` model: inherited Hermes defaults

Do not copy secrets, SMTP passwords, tokens, or private group IDs into documentation or tickets.

## 14. Documentation status and historical context

`V2_ARCHITECTURE_SPEC.md` began as the phased v2 implementation specification. It was reconciled on 2026-09-29 with the current v2.1 runtime and now documents the implemented CLI, historical-context behavior, config-upgrade mechanism, named local-provider model selection, current package/branch status, and completed milestones.

The milestone history is intentionally retained because it explains why safety/checkpoint boundaries exist, but it should no longer be read as an unfinished implementation plan. The README remains authoritative for operational commands and deployment procedures; current code remains authoritative if any future documentation drift appears.

`V2_1_HISTORICAL_CONTEXT_PLAN.md` has also been updated to reflect that live dry-run and scheduled-digest validation have completed and that the remaining work is quality observation.

## 15. Recommended next steps

1. **Quality-observe local Qwen 9B.** Run small dry-runs first, then compare one or more real-sized windows against the quality standard demonstrated by `gpt-6-sol`.
2. **Capture a true cross-checkpoint context case.** Specifically verify that a current short reply such as “Yes” can be grounded by a historical question without re-emitting historical-only material.
3. **Collect several daily digests before prompt tuning.** Change prompt/schema only for repeated, observable failure patterns.
4. **Before merging v2.1:** run the full suite, `git diff --check`, review branch diff against `main`, and perform a dry-run with the intended production model.
5. **Merge/tag only after quality confidence.** `main` should remain the stable v2.0 baseline until that decision is explicit.

## 16. Useful commands

```bash
# Current branch/state
git status --short --branch
git log -5 --oneline --decorate

# Validate config
digest --config ./config.yaml config validate

# Review new optional config defaults
digest --config ./config.yaml config upgrade

# Safe model/digest test
digest --config ./config.yaml run global-ps --last 2h --dry-run

# Normal checkpoint-driven dry-run
digest --config ./config.yaml run global-ps --dry-run

# Workflow state
digest --config ./config.yaml status global-ps

# Transport/scheduler health
digest --config ./config.yaml transport status
digest --config ./config.yaml schedule status

# Local Ollama models
ollama list

# Hermes provider/model definitions
less ~/.hermes/config.yaml

# Tests
pytest -q tests/test_v2_*.py
git diff --check
```

## 17. Handoff principle

Preserve the existing safety and checkpoint guarantees first. The project has reached the stage where operational quality observation is more valuable than adding architecture. Prefer small, evidence-driven changes over generalized “AI memory,” extra model calls, automatic fallback, or broader state machinery unless repeated production evidence shows a concrete need.
