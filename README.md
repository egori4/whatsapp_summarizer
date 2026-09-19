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
| `0.7.0` | Remove lexical semantic gates, require complete-span grounding, bound generated titles, and separate transport failure from one validation repair (current) |
| `0.6.0` | Compact one-call dispositions, instructions, and prompt byte measurements |
| `0.5.0` | One-pass semantic question/issue authority; remove conflicting local lexical gates |
| `0.4.0` | Cross-day unanswered-question carry-forward/resolution state and Phase 3 semantic pilot |
| `0.3.0` | Phase 2 findings: `api_token` privacy hardening and deterministic accountability for source-authored status changes |
| `0.2.1` | Synthetic one-pass quality-evaluation corpus, scoring harness, bounded call accounting, and owner-only evidence controls |
| `0.2.0` | Source-grounding and identifier-privacy hardening for the one-pass digest |
| `0.1.0` | Collector, spool, two-stage and one-pass pipelines, reviewed-artifact delivery, inert scheduler source |

Versioning rules for this repository:

- **Major** — a change to the collector-only boundary, the outbound-deny guard, the policy contract, or the reviewed-artifact delivery gate.
- **Minor** — new or materially changed digest validation, grounding, rendering, privacy projection, or execution-mode behavior.
- **Patch** — bug fixes, test additions, and documentation that do not change validated output.

Only sanitized source, tests, examples, and documentation are committed. Policy, group directory, spool, rendered artifacts, review manifests, logs, credentials, and session data stay Git-ignored and owner-only. Run the full test suite and `git diff --check` before every commit, and obtain independent review for logic, privacy, or delivery-boundary changes.

## Current Version-Controlled Change

**`0.7.0` — Harden semantics, grounding, and failure handling.**

- No English word list can include, exclude, classify, resolve, assign confidence to, or reject a production candidate. The acknowledgement, untrusted-policy-override, hedged-confidence, and status-change vocabularies and every enforcement site they drove are removed: normalization no longer emits `mechanical_ack` or `untrusted_policy_override`, the one-pass runner supplies every normalized current revision to the model, and the renderer no longer rejects a candidate for excluding or hedging a vocabulary match. Structural privacy, secret, JID, URL, opaque-reference, and protected-token matching is unchanged.
- Reader-facing prose is grounded by structure rather than by a negator list. A question, summary, recommendation, action, or limitation must equal exactly one complete mechanically delimited sentence (`.`, `!`, `?`, `;`) of a declared topic source, or that whole source. Mid-sentence clauses, stitched text, and spans that resolve ambiguously to more than one source location are rejected, so a clause can never be lifted out of the polarity, scope, or condition stated around it.
- Generated titles stay readable but bounded: one line of at most 80 characters, no opaque source reference, no privacy placeholder, and no protected value or numeral that is absent from that topic's own validated reader-facing evidence.
- Provider transport failure and validation rejection are separate. Timeout, stale termination, child exit, empty output, prompt-budget, output-budget, and CLI-unavailable outcomes are terminal for the application call, so an identical effective final/fallback configuration cannot duplicate provider work and a distinct fallback is never reached by a transport failure. Only a real validation rejection may trigger exactly one repair, and the repair prompt carries a closed content-free failure code — never an exception string, rejected candidate, provider output, stderr, source text, identity, credential, or path.
- The legacy two-stage pipeline still decides meaning from English vocabulary, so it is explicitly non-production: it cannot register a reviewed artifact, run `delivery-capable`, or deliver a reviewed artifact.
- Source membership, provenance, question/issue, resolution-evidence, privacy, artifact, delivery, and checkpoint validation are unchanged. No model, protected configuration, runtime state, spool, artifact, SMTP, service, or scheduler was accessed for this phase.

## Prior Version-Controlled Changes

**`0.6.0` — Compact the one-call contract.**

- The provider response uses list-form disposition rows with ordinary string references. Local validation requires exactly one valid disposition for every supplied source and rejects unknown, duplicate, or missing references, including nested topic and unanswered-reference fields. A reference of an unexpected JSON type is rejected as a validation failure rather than raising an unhandled type error.
- The provider instructions are a concise numbered semantic contract covering material selection, selectivity, grouping, corrections and supersession, questions/issues, tracked resolution, limitations, actions, uncertainty, and confidence. Only prose that merely restated a deterministic check was dropped: exact-excerpt copying, topic selectivity, and title grounding have no local validator and are retained verbatim. Deterministic grounding, privacy, protected-token, title, retry, cutoff, and runtime behavior are unchanged.
- Source-only measurement reports UTF-8 bytes separately for instructions, serialized schema, projected synthetic sources, and the exact Hermes structured prompt. On the publication-safe 87-source fixture, Phase 1 reduced those components from `7213 / 13837 / 14850 / 36094` bytes to `3709 / 2472 / 14850 / 21225` bytes. A measured `$defs` variant increased the new schema from 2472 to 2609 bytes, so it was not adopted.
- No model, protected configuration, runtime state, spool, artifact, SMTP, service, or scheduler was accessed for this phase.

**`0.5.0` — Remove conflicting one-pass lexical question/issue gates.**

- The one-pass actionable model remains responsible for semantic `QUESTION` and `ISSUE` classification. The renderer no longer overrides those schema-valid classifications with English question/request, technical-domain, problem, or resolution word lists.
- Deterministic validation still requires reader-facing question text to be an exact source excerpt and verifies source ownership, allowed dispositions, topic/unanswered separation, uniqueness, distinct answer evidence, protected values, privacy, and revision-only provenance.
- The model prompt still forbids recasting announcements, instructions, status statements, and directives as questions and limits unanswered entries to unresolved questions/issues. Semantic quality is checked during isolated artifact review instead of being guessed from vocabulary.
- The legacy two-stage pipeline and the separate acknowledgement, untrusted-policy-override, confidence, status-recall, and negation controls were unchanged at that time; version `0.7.0` removes them from every production path.

**`0.4.0` — Add cross-day question state and the Phase 3 semantic pilot.**

- In the one-pass actionable pipeline, accepted unanswered questions and problem statements are retained by stable message identity plus their current immutable revision, then carried into the next nonempty source window without replaying unrelated history. A switch to the legacy two-stage path fails closed while carried state exists because that path has no actionable provenance contract.
- Edits rebind tracked state to the edited revision so it cannot be silently omitted. Revocations remove the tracked item and release its prior source revision for normal raw-message purging.
- Later validated source-backed answers explicitly classify a carried item as `partial` or `resolved`; the presence of a limitation alone does not imply an incomplete answer. A normal question/issue resolution must contain reader-facing evidence grounded in a distinct included answer revision. An edited tracked revision declared as `UPDATE` is the only self-resolution exception. Both question and declarative-issue resolutions are supported, and omission still fails before render-only output or SMTP.
- The one-pass prompt receives only allowlisted state/revision hints: `tracked_item` for tracked sources and an `edit` revision kind for edited actionable sources. Stable message identity and spool metadata remain local.
- Open and partially answered current source revisions are excluded from raw-message purging. Resolved state is removed after its source revision reaches normal retention expiry, and the coupled purge steps execute in one explicit transaction.
- Cross-day state is checked before any legacy two-stage model construction, even with no new pending events. A tracked edit removed by deterministic acknowledgement/policy-override filtering blocks before model construction and must be corrected or revoked at the source before retrying. An answer revoked or superseded before an unknown delivery is reconciled cannot close the tracked item.
- A fully synthetic three-day fixture exercises limited workarounds, version scope, unanswered retention, acknowledgement removal, source-only topics, readable titles, and current-window date labels. A follow-up review corrected its verification-command answer from `partial` to `resolved` because the requested verification capability was fully answered despite a repair limitation.
- The pilot did not invoke a configured model provider. Phase 2 provider-quality acceptance remains pending its separately approved focused rerun.
- Final independent source re-review, deployment, reviewed-artifact acceptance, delivery, service activation, and scheduling remain separate approvals.

The prior `0.3.0` release applied the first Phase 2 quality findings:

- The initial 16-run synthetic smoke test used 19 Hermes calls and did not meet the model-quality gates. Sanitized aggregates and remediation are recorded in [`ONE_PASS_EVALUATION_FINDINGS.md`](ONE_PASS_EVALUATION_FINDINGS.md).
- Shared projection redaction now covers `api_token=...`, closing the concrete privacy regression found in both privacy-window prompts.
- Version `0.3.0` added a deterministic recall net requiring any declarative, unhedged source statement that something was scheduled, confirmed, moved, postponed, or cancelled to be included or uncertain and assigned to a topic or an unanswered entry. Version `0.7.0` removes that lexical net: the model now owns that decision, and no vocabulary match can reject a candidate.
- The privacy corpus now represents actionable guidance as a normal update and a separately stated unresolved problem as unanswered, matching the current schema contract.
- Focused post-remediation Hermes reruns and human unsupported-content review remain separately approval-gated; model quality is not yet accepted.

The `0.2.1` evaluation infrastructure included:

- Eight synthetic public windows cover manager-critical assignments, answered and unanswered questions, actionable guidance with a source-backed limitation, supersession, uncertain field guidance, privacy-sensitive identifier forms, the fabricated migration-topic regression, and all-nonmaterial input.
- Two exploratory runs per window require 16 evaluation runs and 16–32 possible Hermes calls. No Hermes evaluation is authorized merely by this source.
- Required atoms are manager-critical and gate at zero omissions; optional atom recall is reported separately.
- Models are constructed through the existing validated configuration/provider path. Detailed prompts, raw responses, rendered digests, and logs stay owner-only outside Git; only sanitized aggregate findings may be published.
- Evaluation-infrastructure acceptance and model-quality acceptance are separate decisions. See [`ONE_PASS_EVALUATION_SPEC.md`](ONE_PASS_EVALUATION_SPEC.md).

The underlying `0.2.0` accuracy and privacy hardening remains in force:

Grounding:

- Substantive reader prose (question, narrative, recommendation, actions, limitation) must be one complete mechanically delimited normalized source sentence, or the complete normalized source, after identity sanitization; fuzzy matching, cross-source stitching, mid-word excerpts, mid-sentence clauses, and ambiguous spans are rejected.
- Because every atom is a whole sentence or whole source, a prohibition cannot be rendered as an instruction without maintaining an English negator vocabulary.
- Protected technical identifiers use exact token occurrence instead of substring containment, covering short and punctuation-bearing forms such as `v2`, `3PAR`, `SRX_345`, `802.11ax`, and `C++17`.

Question and unresolved-issue classification before `0.5.0` (superseded for one-pass rendering):

- A reader-facing question previously required a model-declared `QUESTION` kind plus a local lexical check on both the cited source and selected excerpt.
- A bare wh- opening previously required interrogative punctuation as part of that local reclassification.
- `ISSUE` entries previously required a local unresolved-problem vocabulary signal and rejected local resolution phrases. Version `0.5.0` removes these one-pass gates; the legacy two-stage pipeline retains its separate lexical compatibility behavior.
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

Set `"pipeline_mode": "one_pass"` only with provider `hermes-openai-codex` and endpoint `local://hermes-cli`. This is the only pipeline permitted to produce a deliverable candidate. The runner bypasses Ollama preclassification so supporting context in the eligible source window is not dropped. The model clusters that window, while the local renderer validates source references, complete-sentence or complete-source normalized and privacy-sanitized excerpts, exact protected-token membership, bounded generated titles, attribution, confidence, and unresolved issues. A mid-sentence, stitched, or ambiguously resolving excerpt is rejected, so an instruction cannot be lifted out of its negation, scope, or condition.

Redaction scope: normalization derives a `redacted_text` field that masks secret-shaped assignments, opaque numeric mentions including optional `+` and device suffixes, supported WhatsApp participant/group/web JID forms, and newsletter/broadcast handles. The same shared identifier sanitizer protects source text quoted by the local renderer. Model prompts use an explicit projection containing opaque per-request source refs, redacted text, timestamps, resolvable opaque reply linkage, a tracked-item boolean where applicable, and an edit-kind hint for edited actionable sources only. Raw message IDs, participant/display metadata, chat identifiers, and runtime metadata remain local. One-pass mode supplies every normalized current source revision to the model and lets the model dispose of each one; if deterministic normalization removes a tracked revision, processing blocks before model construction.

The verified replay policy uses `reasoning_effort: high`, passed explicitly as `hermes chat --reasoning high`; it is not inherited from the active Hermes profile. Credentials remain inside Hermes' supported stored OAuth abstraction.

The one-pass actionable path is structurally isolated into `actionable_schema.py`, `actionable_validate.py`, and `actionable_render.py`. `models.py` retains compatibility entry points for the legacy two-stage pipeline. That legacy path still resolves meaning from English vocabulary and is therefore explicitly non-production: `DigestConfig.require_production_pipeline()` blocks it from `delivery-capable` runs, reviewed-artifact registration, and reviewed-artifact delivery.

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
- `ONE_PASS_EVALUATION_SPEC.md` — synthetic one-pass Phase 2 corpus, scoring gates, call budget, and evidence boundary.
- `ONE_PASS_EVALUATION_FINDINGS.md` — sanitized aggregate Phase 2 results, findings, and remediation status.
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
