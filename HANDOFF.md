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

- Every model prompt builder, including the two-stage classifier, uses an explicit allowlisted projection: opaque source refs and redacted text, plus optional timestamps, opaque reply linkage, a boolean tracked-item hint, and an `edit` revision hint only where the actionable final model needs them. Raw message IDs, participant/display metadata, chat identifiers, and other spool/runtime metadata remain local.
- One-pass generation applies the same local acknowledgement and untrusted-policy-override exclusion as the two-stage path before constructing a final-model prompt.
- Actionable topics own their declared `source_refs`; locally derived attribution is not model-supplied, dependent references cannot silently enlarge ownership, duplicate specifics are rejected, and topic/unanswered source reuse is rejected.
- The external bridge deployment is required to resolve safe sender labels through a bounded in-memory contact-label cache and, when needed, a target-message-scoped group-metadata lookup. That resolver is not implemented or evidenced in this repository; it remains a deployment integration requirement. Numeric identifiers must never be used as fallback labels, and the model projection excludes participant metadata.
- Administrative summaries keep related planning, nominations, and assignments in one topic. Explicit assignments, volunteer paths, and stated priority/time allocation render as source-grounded action bullets; administrative authors use `Reported by` rather than `Asked by`.
- Question and unresolved-issue attribution is model-cited but independently checked. A reader-facing question must cite an in-topic, `INCLUDE`/`UNCERTAIN` source with model-declared semantic kind `QUESTION`; both the cited source and selected excerpt must pass the local technical-question/request check, so a directive cannot be trimmed into a question. A bare wh- opening additionally requires interrogative punctuation, so relative and exclamative clauses are not read as questions. An unanswered entry must cite a distinct `INCLUDE`/`UNCERTAIN` source declared `QUESTION` or `ISSUE`; the cited source must independently look like an open technical question/request or unresolved technical problem statement, explicit resolution language is rejected, and no context reference may be duplicated.
- Prompt projection and reader sanitization share coverage for numeric mentions with optional `+`/device suffixes, participant/group/web JID forms, newsletter/broadcast handles, and opaque placeholders.
- Questions, narrative, recommendations, actions, and limitations must each be a contiguous, word-bounded normalized exact excerpt from their declared sources after deterministic identity sanitization. An excerpt that drops an immediately preceding negator is rejected. Commands, versions, URLs, large identifiers, and punctuation-bearing or short alphanumeric technical identifiers use exact token membership rather than substring matching. Generated titles remain organizational labels. These deterministic checks deliberately prefer a failed candidate and retry over accepting a plausible unsupported paraphrase.
- Unanswered topic labels are internal grouping metadata and are not rendered. An unanswered-only source window is valid, and unanswered entries are capped at eight; only a window with neither retained updates nor validated unanswered items is rejected.
- In the one-pass actionable pipeline, accepted unanswered questions and declarative problem statements are indexed by stable message identity plus their current immutable revision. On a later nonempty window, only current open or partially answered sources are carried into model input; accepted provenance must retain them as unanswered or cite them as source-backed partial/resolved items. An edit rebinds the state to the edited revision, while revocation removes the item and releases retained raw text for normal purging. The legacy two-stage path continues without actionable provenance when no carried state exists and fails closed if a provider-mode switch encounters carried state.
- Actionable provenance v2 records only revision identities plus an explicit validated `partial` or `resolved` transition. A normal question/issue transition requires reader-facing resolution evidence grounded in a distinct included answer revision; an edited tracked revision declared as `UPDATE` is the sole self-resolution exception. A limitation may belong to a complete answer; `partial` additionally requires a source-backed remaining gap. Existing v1 reviewed artifacts remain valid and resolve cited questions using the prior behavior.
- Carried state is checked before any legacy two-stage model construction, including when no new events are pending. If deterministic acknowledgement/policy-override filtering removes a tracked edited revision, generation fails before model construction with recovery guidance. Purge changes are one explicit transaction, and an existing spool target is checked before the Phase 3 schema migration. A revoked or superseded answer cannot close a tracked item during later delivery reconciliation.
- [`POLICY_CONTRACT.md`](POLICY_CONTRACT.md) records the schema-v1 field-use matrix and distinguishes runtime controls from fixed or compatibility declarations.

## Phase 3 semantic pilot

- Version `0.4.0` adds cross-day open-question state and the fully synthetic three-day fixture `tests/fixtures/phase3_semantic_pilot.json`.
- The isolated pilot covers a limited workaround with its limitation, a release-specific answer that is not generalized, retained and carried unanswered questions, irrelevant acknowledgement removal, source-supported topics only, and readable generated titles.
- Follow-up read-only reviews found edit/revocation lifecycle defects, a missing declarative-issue resolution path, limitation-based completeness inference, carried-source date-range expansion, and a self-resolution hole that allowed a question to close without distinct answer evidence. Regression-first remediation now covers those cases; the verification-command fixture is correctly `resolved` even though the command has a repair limitation.
- All three deterministic daily candidates pass production rendering, grounding, provenance, state-transition, and current-window date validation after remediation. Final independent source re-review remains the commit gate.
- This source-level pilot did not invoke Hermes or another configured provider and does not supersede the pending Phase 2 focused provider rerun. It did not access protected policy, operational spools, SMTP, WhatsApp, the gateway, or a scheduler.
- Deployment, artifact acceptance, delivery, service restart, and scheduling remain separately approval-gated.

## Phase 2 one-pass quality evaluation

- Version `0.3.0` includes the source-only evaluation infrastructure plus narrow privacy-redaction and manager-critical announcement validation changes found by the first smoke test. The infrastructure-only baseline was `0.2.1` in scope.
- A publication-safe corpus contains eight synthetic windows covering manager-critical announcements/assignments, answered and unanswered questions, actionable guidance with a limitation, supersession, uncertain field guidance, privacy-sensitive identifier forms, the fabricated migration-topic regression, and an all-nonmaterial window.
- Two runs per window mean 16 evaluation runs and 16–32 possible Hermes calls. This is an exploratory repeatability check, not a statistical reliability estimate.
- The evaluator uses the existing validated config/provider builder and one-pass prompt/schema/validator/renderer. Detailed prompts, raw responses, rendered digests, validation details, and logs must remain owner-only outside Git.
- The approved initial evaluation completed all 16 runs with 19 Hermes calls. It did not meet the quality gates: aggregate required-atom recall was 95%, one resolution-placement expectation was incorrect, and the two privacy prompts exposed one missed synthetic secret form. No known migration fabrication or superseded advice was accepted; both no-material runs failed closed.
- The expectation defect is corrected. Focused regressions now cover `api_token` redaction and manager-critical technical/engineering administrative status retention. The focused rerun must use the current explicit resolved/partial schema documented in `ONE_PASS_EVALUATION_SPEC.md`; model-quality acceptance remains pending a separately approved rerun plus human artifact review.
- No-material runs can measure fabrication versus fail-closed rejection only; they do not validate checkpoint behavior without runner/spool coverage.
- [`ONE_PASS_EVALUATION_SPEC.md`](ONE_PASS_EVALUATION_SPEC.md) defines the corpus, rubric, acceptance gates, artifact boundary, and separate infrastructure/model-quality decisions. Historical Ollama authorization in [`EVALUATION_SPEC.md`](EVALUATION_SPEC.md) does not authorize Hermes.
- [`ONE_PASS_EVALUATION_FINDINGS.md`](ONE_PASS_EVALUATION_FINDINGS.md) records only sanitized aggregate results, findings, and remediation; detailed model artifacts remain owner-only outside Git.

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
