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

- Version `1.0.0` adds operator-only bounded-prefix recovery. One explicit durable cutoff selects a nonempty contiguous prefix after the current checkpoint; that cutoff is bound through source selection, provenance, artifact identity, delivery checks, and checkpoint advancement. Bounded artifacts carry a visible historical-window warning, and later rows remain pending.
- The scheduler and unattended wrapper provide no bounded-cutoff or review-bundle arguments. Bounded recovery is accepted only for an explicit `render-only` review bundle and cannot be inferred by a delivery-capable or unattended path.
- Render-only review bundles may now contain an exact owner-only artifact plus a canonical portable envelope. The envelope carries only checkpoint/cutoff, artifact/policy/target/provenance hashes, run/model/count/coverage facts, and revision-only provenance; it contains no message text, rendered prose, recipient, credential, or SMTP data. Both bundle files must be regular owner-only `0600` files at delivery time.
- The live spool independently validates and consumes a portable envelope before SMTP. It rejects altered, absent, weak-permission, stale, wrong-policy, wrong-target, wrong-checkpoint/cutoff, missing, revoked, or mismatched evidence, and replays of an accepted or unresolved snapshot. The envelope's hashes are unkeyed, so its source count, candidate count, and run type are recomputed from live sources and its coverage metadata is replaced by live coverage before anything is recorded. Successful acceptance advances only the live checkpoint to the reviewed cutoff; uncertain SMTP retains it and is terminal, while a transport failure retains it and leaves the same artifact retryable.
- A bounded window is validated against the prefix state at its cutoff at both render and delivery time. A later edit to an in-prefix source therefore stays pending instead of blocking the reviewed window; a revoked source blocks before the model call and again before SMTP.
- Phase 3 source and synthetic tests made no model call, protected-policy/spool access, operational render, SMTP attempt, service change, or scheduler change. Independent source acceptance remains the next gate; Phase 4 has not started.

- Every model prompt builder, including the two-stage classifier, uses an explicit allowlisted projection: opaque source refs and redacted text, plus optional timestamps, opaque reply linkage, a boolean tracked-item hint, and an `edit` revision hint only where the actionable final model needs them. Raw message IDs, participant/display metadata, chat identifiers, and other spool/runtime metadata remain local.
- One-pass generation supplies every normalized current source revision to the model. No local acknowledgement, untrusted-policy-override, hedged-confidence, or status-change vocabulary removes, requires, or rejects a source; untrusted source content is contained structurally by schema, reference, grounding, privacy, and delivery validation instead.
- Actionable topics own their declared `source_refs`; locally derived attribution is not model-supplied, dependent references cannot silently enlarge ownership, duplicate specifics are rejected, and topic/unanswered source reuse is rejected.
- The external bridge deployment is required to resolve safe sender labels through a bounded in-memory contact-label cache and, when needed, a target-message-scoped group-metadata lookup. That resolver is not implemented or evidenced in this repository; it remains a deployment integration requirement. Numeric identifiers must never be used as fallback labels, and the model projection excludes participant metadata.
- Administrative summaries keep related planning, nominations, and assignments in one topic. Explicit assignments, volunteer paths, and stated priority/time allocation render as source-grounded action bullets; administrative authors use `Reported by` rather than `Asked by`.
- In the one-pass actionable path, the model owns semantic `QUESTION`/`ISSUE` classification. Deterministic validation does not reclassify those sources with English vocabulary lists. A reader-facing question must still be one exact excerpt from its cited in-topic, `INCLUDE`/`UNCERTAIN` source; unanswered entries must cite a distinct `INCLUDE`/`UNCERTAIN` source; topic/unanswered overlap, duplicate unanswered sources, invented question text, and invalid resolution evidence still fail closed. The legacy two-stage path retains its existing lexical compatibility behavior.
- Prompt projection and reader sanitization share coverage for numeric mentions with optional `+`/device suffixes, participant/group/web JID forms, newsletter/broadcast handles, and opaque placeholders.
- Questions, narrative, recommendations, actions, and limitations must each equal one complete mechanically delimited normalized source sentence, or the complete normalized source, after deterministic identity sanitization, and must resolve to exactly one source span. Mid-sentence clauses, stitched text, and ambiguous spans are rejected, which preserves polarity and scope without an English negator vocabulary. Commands, versions, URLs, large identifiers, and punctuation-bearing or short alphanumeric technical identifiers use exact token membership rather than substring matching. Generated titles are organizational labels bounded to one line of at most 80 characters that may not introduce an opaque source reference, privacy placeholder, protected value, or numeral absent from that topic's validated reader-facing evidence. These deterministic checks deliberately prefer a failed candidate over a plausible unsupported paraphrase.
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

## One-pass lexical-gate repair

- Version `0.5.0` removes the local English question/request and technical-problem vocabulary gates from one-pass actionable rendering. This fixes a failure mode in which a schema-valid, source-grounded model classification could be rejected after inference.
- Synthetic regressions cover both prior failure categories: a model-declared `QUESTION` outside the local question/technical vocabulary and a model-declared `ISSUE` outside the local technical/problem vocabulary.
- Exact-source grounding, source ownership, allowed dispositions, topic/unanswered separation, uniqueness, distinct answer evidence, revision-only provenance, privacy validation, and checkpoint fail-closed behavior remain in force.
- This narrow repair did not change acknowledgement filtering, untrusted-policy-override filtering, confidence handling, status-change recall, negation grounding, or the legacy two-stage pipeline. Version `0.7.0` completes that work.
- Hermes adapter failures carry only content-free diagnostics (category, stage, child return code when available, actual elapsed duration, and byte counts). The configured timeout is the parent process's hard deadline; Hermes `--run-budget` is advisory and does not itself guarantee child termination. Since `0.7.0` every transport failure category is terminal for the application call, so neither an identical nor a distinct fallback configuration can duplicate provider work.
- Source tests pass without invoking a model or touching protected runtime state. An isolated owner-only `render-only` replay, human artifact review, source deployment, service restart, and delivery retry remain separate explicit approvals.

## Redesign Phase 2 semantic, grounding, and failure-handling boundary

- Version `0.7.0` removes every production selection or rejection effect of the local acknowledgement, untrusted-policy-override, hedged-confidence, and status-change vocabularies, including the stage-zero exclusion, both status-change rejections, and the low-confidence rejection. Normalization no longer emits `mechanical_ack` or `untrusted_policy_override`, and the one-pass runner passes the whole normalized window to the model.
- Reader-facing grounding is structural: each factual atom must equal exactly one complete mechanically delimited source sentence (`.`, `!`, `?`, `;`) or the complete source, and must resolve to a single span. This preserves polarity, scope, and conditions without an English negator list.
- Generated titles are validated separately against that topic's own validated reader-facing evidence, with bounded length, no opaque source reference, no privacy placeholder, and no unsupported protected value or numeral.
- Transport failure and validation rejection are separated. Timeout, stale termination, child exit, empty output, prompt-budget, output-budget, and CLI-unavailable outcomes end the run after exactly one application call. Only a real validation rejection may trigger one repair, whose prompt carries a closed content-free failure code and never an exception string, rejected candidate, provider output, stderr, source text, identity, credential, or path.
- The legacy two-stage pipeline retains its lexical compatibility behavior and is now explicitly non-production: it is refused for reviewed-artifact registration, `delivery-capable` runs, and reviewed-artifact delivery.
- Source membership, provenance, question/issue, resolution-evidence, privacy, artifact, delivery, and checkpoint validation are unchanged. This phase made no model or operational call and did not access protected data.

## Phase 1 one-call contract compaction

- Version `0.6.0` changes the one-pass provider response to list-form disposition rows. Deterministic validation requires exact supplied-source coverage and rejects unknown, duplicate, or missing disposition and nested references before rendering or provenance construction.
- The provider-facing instructions are now a concise numbered contract that retains model-owned materiality, selectivity, grouping, correction/supersession, question/issue, resolution, limitation, action, uncertainty, and confidence decisions while leaving exact structural enforcement to local validation. Rules with no deterministic validator are retained verbatim in the contract, including exact-excerpt copying, topic selectivity, and title grounding.
- Publication-safe synthetic measurements report instruction, schema, projected-source, and exact total prompt bytes independently. For 8 sources, the components changed from `7213 / 3409 / 1344 / 12160` to `3709 / 2470 / 1344 / 7717`; for 87 sources, from `7213 / 13837 / 14850 / 36094` to `3709 / 2472 / 14850 / 21225`.
- A deterministic 87-source comparison found `$defs`/`$ref` increased the compact schema from 2472 to 2609 bytes, so ordinary strings plus exact local membership checks remain the smaller contract.
- Malformed provider references of any JSON type fail closed as a validation rejection rather than an unhandled type error.
- Phase 1 does not change lexical gates, retry behavior, grounding/span rules, title behavior, bounded-prefix behavior, or runtime configuration. It made no model or operational call and did not access protected data.

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
