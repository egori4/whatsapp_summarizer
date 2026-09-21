# Policy Contract (schema v1)

This document is the field-use matrix for the checked-in policy schema. It is a **source-contract record**, not permission to activate a timer, delivery, model run, policy change, or service restart.

## Design decision: constrain and document

Schema v1 deliberately keeps safety-critical values fixed rather than exposing them as live tuning knobs. Fields with no runtime consumer are retained only for compatibility with existing protected policy files and are explicitly non-operative. They must not be represented as effective controls in future documentation.

A future schema migration may either wire a field with deterministic tests or remove it from both the schema and all protected policies. That migration requires a separate design and operational approval. It is outside this remediation.

## Field-use matrix

### Runtime-consumed fields

- `target_group_jid` and `whatsapp.group_allow_from`: parsed and required to be the same sole immutable group identifier; used to bind the spool and collector boundary.
- `silence`: parsed as fixed all-output-deny invariants; the collector runtime enforces the corresponding collector-only boundary.
- `paths.spool` and `paths.state_dir`: consumed by the collector plugin at registration to create and bind its durable spool. The command runner separately takes explicit `--spool` and lock paths.
- `models.provider`, `models.pipeline_mode`, model IDs, endpoint, timeouts, batch/context/output limits, instructions, and reasoning effort: parsed; consumed by model construction or local prompt/model limits. The actionable `one_pass` and `two_call` modes are restricted to the Hermes Codex connector; the legacy `two_stage` mode remains non-production.
- `external_fallback`: parsed as a fail-closed invariant; any enabled value is rejected.
- `smtp`: parsed; used only by the separately gated delivery path. SMTP settings alone do not authorize delivery.

### Fixed safety assertions, not dynamic controls

- `schedule.timezone`, `schedule.expression`, and `schedule.activate_only_after_dst_contract`: must equal the fixed `08:00 America/Toronto` declaration and DST gate. No scheduler reads or creates jobs from these fields.
- `retention.raw_days` and `retention.digest_days`: must equal `7` and `90`. `DurableSpool.purge()` uses matching code constants, preserving the checkpoint-aware retention boundary without permitting unreviewed retention changes.
- `paths.mode`: must equal `0700`; it asserts the owner-only state-directory contract.
- `redaction.enabled`: must be `true`. Redaction is mandatory and cannot be disabled by policy; Stage 1 also re-applies secret redaction at the final-model projection boundary.

### Compatibility declarations with no runtime effect

- `runtime.lock_seconds` and `runtime.max_runtime_seconds`: positive-value declarations only. Mutual exclusion is enforced by the explicit `flock`; there is no in-process runtime deadline.
- `health.heartbeat_seconds`: positive-value declaration only. Health is queried on demand from the spool; it is not an active heartbeat interval.
- `contacts.fallback`: retained for schema compatibility only. Reader attribution is derived locally as `display_name`, then participant, independent of this value.

## Operational consequence

The checked-in policy example and protected production policy must continue to parse under schema v1. This contract does **not** change retention cleanup, schedule behavior, recipient selection, state paths, model selection, or any live process. Any change to those behaviors remains a separately approved operational task.
