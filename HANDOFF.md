# Public Project Handoff

## Repository status

This repository contains the source code, tests, and sanitized examples for a collector-only WhatsApp technical-digest pipeline. It intentionally excludes all operational state.

Not present in Git:

- WhatsApp group directories, JIDs, participant information, or message bodies;
- active policies, SMTP credentials, OAuth material, bridge sessions, or local configuration;
- SQLite spools, rendered digests, review manifests, logs, locks, and rollback backups;
- personal paths and production service identities.

## Safety model

- The collector is never a conversational bot.
- A deployment must pin exactly one immutable group JID in an owner-only, Git-ignored policy. Display-name matching, wildcards, and aliases are prohibited.
- The outbound guard must deny every WhatsApp output primitive, including unknown POST routes.
- The bridge and local model endpoints must remain loopback-only.
- The runner defaults to `validate-only`; rendering, reviewed-artifact delivery, service restart, and scheduling remain separate operator approvals.
- SMTP delivery must use the fixed recipient declared in the protected local policy. The published code deliberately contains no recipient identity or credentials.

## Current source hardening

- Every model prompt builder, including the two-stage classifier, uses an explicit allowlisted projection: opaque source refs and redacted text, plus optional timestamps and opaque reply linkage only where the final model needs them. Raw message IDs, participant/display metadata, chat identifiers, and spool/runtime metadata remain local.
- One-pass generation applies the same local acknowledgement and untrusted-policy-override exclusion as the two-stage path before constructing a final-model prompt.
- Actionable topics own their declared `source_refs`; locally derived attribution is not model-supplied, dependent references cannot silently enlarge ownership, duplicate specifics are rejected, and topic/unanswered source reuse is rejected.
- The external bridge deployment is required to resolve safe sender labels through a bounded in-memory contact-label cache and, when needed, a target-message-scoped group-metadata lookup. That resolver is not implemented or evidenced in this repository; it remains a deployment integration requirement. Numeric identifiers must never be used as fallback labels, and the model projection excludes participant metadata.
- Administrative summaries keep related planning, nominations, and assignments in one topic. Explicit assignments, volunteer paths, and stated priority/time allocation render as source-grounded action bullets; administrative authors use `Reported by` rather than `Asked by`.
- Question and unresolved-issue attribution is model-cited but independently checked. A reader-facing question must cite an in-topic, `INCLUDE`/`UNCERTAIN` source with model-declared semantic kind `QUESTION`; both the cited source and selected excerpt must pass the local technical-question/request check, so a directive cannot be trimmed into a question. A bare wh- opening additionally requires interrogative punctuation, so relative and exclamative clauses are not read as questions. An unanswered entry must cite a distinct `INCLUDE`/`UNCERTAIN` source declared `QUESTION` or `ISSUE`; the cited source must independently look like an open technical question/request or unresolved technical problem statement, explicit resolution language is rejected, and no context reference may be duplicated.
- Prompt projection and reader sanitization share coverage for numeric mentions with optional `+`/device suffixes, participant/group/web JID forms, newsletter/broadcast handles, and opaque placeholders.
- Questions, narrative, recommendations, actions, and limitations must each be a contiguous, word-bounded normalized exact excerpt from their declared sources after deterministic identity sanitization. An excerpt that drops an immediately preceding negator is rejected. Commands, versions, URLs, large identifiers, and punctuation-bearing or short alphanumeric technical identifiers use exact token membership rather than substring matching. Generated titles remain organizational labels. These deterministic checks deliberately prefer a failed candidate and retry over accepting a plausible unsupported paraphrase.
- Unanswered topic labels are internal grouping metadata and are not rendered. An unanswered-only source window is valid, and unanswered entries are capped at eight; only a window with neither retained updates nor validated unanswered items is rejected. The next source milestone is minimal cross-day carry-forward and resolution tracking for validated unanswered questions.
- [`POLICY_CONTRACT.md`](POLICY_CONTRACT.md) records the schema-v1 field-use matrix and distinguishes runtime controls from fixed or compatibility declarations.

## Publication-safe setup

1. Copy `config/digest.policy.example.json` to the ignored `config/digest.policy.json`.
2. Supply the real immutable target JID, allowlist, local state path, SMTP transport, and recipient only in that owner-only local policy.
3. Configure the Hermes gateway’s matching allowlist and collector target through the deployment guide, using the same JID.
4. Run the test suite and static checks before any activation.
5. Keep all runtime files ignored. Do not commit policy files, group metadata, SQLite databases, render artifacts, or credentials.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q
git diff --check
```

These checks do not send a message, invoke a model, send email, restart a service, or activate a schedule.

## Local operator note

Detailed production evidence and rollback records belong in an owner-only operational handoff outside version control. They must never be copied into this repository.
