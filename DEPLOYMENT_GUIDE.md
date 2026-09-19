# Deployment Guide

## Purpose and publication boundary

This is a **publication-safe deployment guide** for the WhatsApp Tech Digest source tree. It describes approval gates and artifacts only. It contains no target identity, recipient, credential, local path, service name, live status, runtime evidence, or activation command.

The project is collector-only: it must not send WhatsApp messages, create a conversational bot, or expose collected data through a public interface.

For source/runtime boundaries, read [`README.md`](README.md), [`AGENTS.md`](AGENTS.md), and [`HANDOFF.md`](HANDOFF.md) first.

## Required separation of concerns

The following stages are distinct and must not be conflated:

1. **Source review** — validate source, tests, documentation, and publication safety.
2. **Protected local configuration** — create or update the ignored owner-only policy; never commit it.
3. **Read-only preflight** — verify the exact target, bridge boundary, policy consistency, and durable-state safety.
4. **Isolated rendering** — use an isolated spool and `render-only` mode to inspect an artifact without SMTP or checkpoint changes.
5. **Artifact acceptance** — approve a specific rendered artifact.
6. **Delivery authorization** — separately approve delivery of that accepted artifact.
7. **Activation or scheduling** — separately approve service changes, restarts, or timer activation.

Artifact acceptance never authorizes delivery, deployment, restart, activation, or cleanup.

## Protected local configuration

The repository ships only `config/digest.policy.example.json`, which is intentionally nondeployable. A real local policy must remain owner-only and ignored by Git.

A protected policy is responsible for values such as:

- the one immutable approved group identifier;
- matching collector and gateway allowlists;
- owner-only state location;
- fixed delivery recipient and credential reference;
- provider and pipeline selection;
- retention and runtime limits.

Never place these values in source files, documentation, test fixtures, commit messages, or public issue text.

## Source-only preflight

Before any protected configuration or operational action, perform the following non-operational checks from a clean review environment:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q
git diff --check
git status --short
```

Review the staged and unstaged diff for policies, credentials, message data, participant identifiers, runtime artifacts, local paths, and generated files. They must not be committed.

## Staged deployment model

### Stage A — source acceptance

Acceptance criteria:

- tests and static checks pass;
- public documentation is internally linked and contains no live operational material;
- the collector-only and no-output constraints are preserved;
- an independent review accepts the source artifact.

This stage has no runtime effect.

### Stage B — protected configuration preflight

An operator performs read-only verification that the local ignored policy, collector, and gateway are consistently scoped to the same exact target. Confirm that state is bound to that target and that existing data will not be reused for another target.

Do not activate services, invoke models, deliver mail, or alter state during this stage.

### Stage C — isolated artifact validation

Run only against an isolated owner-only copy of the spool in `render-only` mode. Validate that substantive output uses exact normalized, privacy-sanitized source excerpts, excludes identifiers and secrets, represents actionable updates, and retains unanswered technical questions in their own section.

A render-only result must not contact SMTP or advance a production checkpoint.

For an operator-approved bounded historical recovery, select one explicit durable cutoff and write both the exact owner-only artifact and its owner-only portable envelope. The artifact must be reviewed as historical because later pending changes may correct or supersede its advice. Do not copy a reviewed-artifact database row between spools.

### Stage D — delivery authorization

Delivery-capable execution is allowed only after explicit approval for the exact reviewed artifact. The live spool must independently verify the portable envelope, exact artifact bytes, protected-policy and target bindings, checkpoint/cutoff, delivery/replay state, and every immutable provenance revision before SMTP. It must send the approved bytes rather than rerun a model or render against production. Acceptance advances only to the envelope cutoff; later rows remain pending.

The source tree also contains a distinct unattended wrapper that can generate and deliver in one `delivery-capable` run. It bypasses exact-artifact human review and therefore requires a separate explicit operating-policy decision and activation approval; the presence of that inert source is not authorization to use it.

SMTP acceptance is relay evidence only; it is not proof of inbox placement.

### Stage E — activation and scheduling

Any service restart, runtime activation, scheduler enablement, or infrastructure change requires a separate explicit approval after a fresh read-only preflight. Document rollback and recovery implications before acting.

## Rollback principles

- Do not delete source, spool, logs, or evidence as part of a rollback unless explicitly authorized.
- If configuration consistency is uncertain, stop before activation and preserve evidence for diagnosis.
- If an artifact cannot be matched to its approved manifest, block delivery rather than regenerate or substitute it.
- A failed model, validation, delivery, or checkpoint condition must fail closed and retain messages for a safe retry.

## Related documentation

- [`RUNBOOK.md`](RUNBOOK.md) — safe verification and incident-response procedures.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — source architecture and evidence map.
- [`POLICY_CONTRACT.md`](POLICY_CONTRACT.md) — configuration field-use contract.
- [`DAILY_SCHEDULER_DESIGN.md`](DAILY_SCHEDULER_DESIGN.md) — design constraints for future cadence.
