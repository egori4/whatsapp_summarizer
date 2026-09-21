# Digest Redesign Implementation Plan

## Status and purpose

This is a source-level execution plan for the approved design in
[`DIGEST_REDESIGN.md`](DIGEST_REDESIGN.md). It does not itself authorize a
model call, protected-spool access, SMTP, policy changes, service changes,
scheduler changes, delivery, or cleanup.

The plan is intentionally limited to six controlled phases plus one
conditional branch. Each phase is small enough for a fresh AI session, ends at
an explicit gate, and has a separate read-only review prompt. Do not continue
to the next phase until the current phase is accepted.

## Why these model assignments

The assignments below distinguish two different models:

- the **session model** edits or reviews this repository; and
- the **digest runtime model** is the policy-pinned Hermes GPT-5.6 Terra/High
  adapter being measured. Changing the session model never changes the runtime
  model.

OpenAI describes GPT-5.6 Sol as its GPT-5.6 flagship for complex professional
work and GPT-5.6 Terra as the balance of intelligence and cost. Anthropic
describes Claude Opus as suited to complex agentic coding and Claude Sonnet as
the speed/intelligence balance. Therefore the plan uses Sol or Opus for the
cross-cutting source changes, and Terra or Sonnet for bounded execution or
review where the design is already fixed.

Use **High** reasoning or the closest available high/extended-thinking setting.
Higher settings are not recommended by default: the work benefits more from
small phases, explicit tests, and independent review than from unbounded
reasoning. If a named model is unavailable, stop and let the operator choose a
replacement; do not silently substitute one.

This phasing follows evaluation guidance to define the objective and pass/fail
metric before a run, test each stage, use task-specific realistic cases, and
combine deterministic metrics with human judgment:

- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
- [Anthropic model overview](https://platform.claude.com/docs/en/models/overview)

## Rules for every phase

1. Start the session in the existing repository worktree. Do not create a new
   worktree, reset, revert, clean, or discard existing changes.
2. First read `AGENTS.md`, `HANDOFF.md`, `README.md`,
   `DEPLOYMENT_GUIDE.md`, `RUNBOOK.md`, `DIGEST_REDESIGN.md`, and this document.
3. Inspect `git status` and the complete current diff before editing. Existing
   changes belong to the operator.
4. Add a failing regression test before each behavior change. Documentation or
   measurement-only work does not require a contrived test.
5. Keep private operational evidence owner-only and outside Git. Never print
   or report prompts, responses, message text, identities, credentials,
   policies, recipient data, runtime paths, spool contents, artifacts, or
   session data.
6. Run focused tests while developing. Before handing off any source phase,
   run:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -q
   git diff --check
   ```

7. Do not commit unless the operator explicitly requests a commit.
8. Stop after the phase report. Do not begin the next phase automatically.
9. If a gate fails, preserve private evidence, report only the sanitized
   failure category and call count, and stop.
10. A reviewer is read-only unless the operator separately asks that reviewer
    to implement corrections.
11. The operator decides who reviews a phase, may ask a reviewer to implement
    its own findings, and may continue in the same session. Acceptance is the
    operator's decision and is recorded only in the review acceptance record
    below. Confirm that record before starting a phase that depends on it.

The phase report must always include: files changed, tests run and totals,
`git diff --check` result, the stated success gate, PASS/FAIL, and any remaining
blocker. Operational reports must additionally include only sanitized call
counts, timing categories, byte/token aggregates, and validation categories.

## Review acceptance record

This table is the single source of truth for every "confirm the earlier phase
was accepted" gate in this plan. A row accepts the tree it names; if that
phase's source changes afterwards, the operator records a new row. Only the
operator adds rows.

| Phase | Accepted tree | Basis | Date |
| --- | --- | --- | --- |
| 1 | `ab4c8d3` | operator acceptance | 2026-09-20 |
| 2 | `ab4c8d3` | operator acceptance | 2026-09-20 |
| 3 | `ab4c8d3` | independent review returned REVISE with three blockers; the same reviewer implemented the fixes at the operator's request; full suite passes | 2026-09-20 |
| C | `108e531` plus `9bbec2d` | independent re-review returned APPROVE after the four-blocker remediation; the post-qualification watchdog/accounting review returned APPROVE SOURCE FIX; the ownership delta review returned APPROVE OWNERSHIP DELTA; operator acceptance | 2026-09-21 |

Phases 1-3 and Conditional Phase C are accepted against the current working
tree. That acceptance authorized the first Phase 4 two-call qualification; it
did not itself authorize the later explicitly approved retry recorded below.

## Phase map

| Phase | Result | Session model | Runtime calls |
| --- | --- | --- | ---: |
| 1 | Compact schema/instructions and add prompt measurements | GPT-5.6 Sol / High | 0 |
| 2 | Remove lexical semantic gates and fix retry/span/title rules | Claude Opus / High | 0 |
| 3 | Add bounded-prefix recovery and isolated-to-live artifact binding | GPT-5.6 Sol / High | 0 |
| 4 | Independent source acceptance and synthetic qualification | GPT-5.6 Terra / High | one-call: 1; two-call attempts: 1 transport stop, then 3-call validation stop |
| 5 | Three isolated realistic-window trials and architecture decision | Claude Opus / High | one-call: 3/6; two-call: 6/9 |
| C | Implement ephemeral two-call path, only after a failed gate | Claude Opus / High | 0 during implementation |
| 6 | Verify and deliver one exact approved artifact | GPT-5.6 Sol / High | 0 model calls; 1 SMTP attempt |

The maximums in Phases 4 and 5 allow one targeted repair per attempted window
only after a real validation rejection. A timeout, stale termination, child
exit, empty output, or output-budget failure never authorizes another
application call.

---

## Phase 1 — Compact the one-call contract

### Goal

Reduce avoidable input/output overhead without changing which facts the digest
must preserve.

Implement only:

- list-form dispositions with exact local set validation;
- unknown, duplicate, and missing reference rejection at every nesting level;
- a concise numbered provider instruction contract that retains all semantic
  decisions but removes prose already enforced by deterministic validation;
- separate byte measurements for instructions, schema, projected sources, and
  total prompt; and
- before/after measurements using publication-safe synthetic fixtures.

Use `$defs`/`$ref` only if a deterministic measurement shows a material size
reduction and existing model/schema handling supports it. The list form plus
local membership checks is sufficient otherwise. Do not change lexical gates,
retry behavior, span rules, titles, cutoff behavior, or runtime configuration
in this phase.

### Execution prompt

**Recommended session model: GPT-5.6 Sol, High reasoning.**

```text
Continue the existing WhatsApp Tech Digest worktree. This is a fresh session.
First read AGENTS.md, HANDOFF.md, README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, then follow
only Phase 1. Inspect git status and the complete existing diff; preserve all
existing work and do not reset, revert, clean, commit, or create another
worktree.

Implement Phase 1 test-first: compact the one-call schema to list-form
dispositions with exact source-set and nested-reference validation, simplify
the provider instruction block without removing semantic responsibilities, and
measure instruction/schema/source/total prompt bytes separately on synthetic
fixtures. Use $defs/$ref only if measured and supported. Add regression tests
for missing, duplicate, and unknown references and for the measurement fields.

Do not invoke Hermes or any model. Do not access protected configuration,
runtime state, spools, artifacts, SMTP, services, or the scheduler. Do not
change lexical gates, retry policy, grounding/span behavior, title behavior, or
bounded-prefix behavior in this phase.

Run focused tests, the full unit suite, and git diff --check. Review the full
source diff for privacy and accidental scope expansion. Stop and return the
standard sanitized phase report with before/after byte totals from synthetic
data only. Do not start Phase 2.
```

### Success gate

- New regressions fail before and pass after the implementation.
- All source and nested references are validated locally against the exact
  supplied set.
- The instruction contract still names every model-owned semantic decision in
  the redesign.
- Synthetic measurements show the component sizes without exposing content.
- Full tests and `git diff --check` pass.

### Independent review prompt

**Recommended reviewer: Claude Sonnet, High/extended thinking.**

```text
Perform a read-only independent review of Phase 1 in the existing WhatsApp
Tech Digest worktree. Read AGENTS.md, HANDOFF.md, README.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, especially
Phase 1. Inspect git status, the complete diff, the schema/prompt builders,
validators, and all new tests. Do not edit files, invoke a model, access
protected runtime data, send SMTP, or change services.

Verify that list-form dispositions have exact local set validation at every
reference site, the shorter instruction contract did not delete a required
semantic responsibility, measurements are content-free, and no out-of-scope
behavior changed. Run the focused and full unit tests plus git diff --check.
Return APPROVE or REVISE, with only concrete blockers and file/line references.
```

---

## Phase 2 — Harden semantics, grounding, and failure handling

### Goal

Complete the no-lexical-classification boundary and ensure one provider
transport failure cannot launch duplicate work.

Implement together because they meet in the renderer/validator boundary:

- remove production effects of `_ACKS`, `_ACK_ONLY`, `_POLICY_OVERRIDE`,
  `_LOW_CONFIDENCE`, and `_STATUS_CHANGE` / `announces_status_change`;
- cover the actual stage-zero exclusion, status-change rejection, and both
  low-confidence rejection sites, not only constant definitions;
- replace the negator-prefix list with unique complete-sentence or complete-
  normalized-source grounding;
- allow readable model-generated titles with the bounded privacy/protected-
  token checks from the redesign;
- distinguish transport failures from validation rejection;
- make timeout, stale termination, child exit, empty output, and output-budget
  failure terminal for the application call;
- permit at most one targeted repair after an actual validation rejection,
  using only a closed content-free failure-code enum; and
- prevent identical effective final/fallback configurations from causing a
  second provider call.

Structural privacy, secret, JID, URL, and protected-token matching remains.
Do not weaken source membership, provenance, question/issue, resolution,
privacy, artifact, delivery, or checkpoint validation.

This boundary applies to every path that remains selectable for production. If
the legacy two-stage path is retained without redesign, it must become
explicitly non-production rather than preserve a hidden lexical alternative.

### Execution prompt

**Recommended session model: Claude Opus, High/extended thinking.**

```text
Continue the existing WhatsApp Tech Digest worktree. This is a fresh session.
First read AGENTS.md, HANDOFF.md, README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, then follow
only Phase 2. Inspect git status and the complete existing diff; preserve all
existing work and do not reset, revert, clean, commit, or create another
worktree. Confirm that Phase 1 has an accepted review; if not, stop.

Implement Phase 2 test-first exactly as specified. Remove every production
selection or rejection effect of the named lexical semantic lists, including
the actual stage-zero, status-change, and two confidence enforcement sites.
Keep structural privacy and protected-token checks. Replace negator vocabulary
matching with unique complete-sentence-or-complete-source grounding. Add
bounded safe generated-title validation. Separate transport failures from
validation repair, use a closed content-free repair-code enum, and prove that
identical effective configurations cannot cause a duplicate application call.

Add exact regressions for each removed enforcement path, polarity-preserving
span boundaries, title safety, each sanitized failure category, no transport
retry, and one validation-only repair. Never put exception strings, rejected
output, prompts, responses, stderr, source text, identities, credentials, or
paths into diagnostics or repair prompts.

Do not invoke Hermes or any model. Do not access protected configuration,
runtime state, spools, artifacts, SMTP, services, or the scheduler. Do not add
bounded-prefix recovery yet.

Run focused tests, the full unit suite, and git diff --check. Stop with the
standard sanitized phase report. Do not start Phase 3.
```

### Success gate

- A vocabulary match cannot include, exclude, classify, resolve, assign
  confidence, or reject a production candidate.
- Structural safety checks remain and all reader facts resolve to one complete
  allowed span.
- All transport categories stop after one application call.
- Only a validation rejection can trigger one closed-code repair.
- Full tests and `git diff --check` pass.

### Independent review prompt

**Recommended reviewer: GPT-5.6 Sol, High reasoning.**

```text
Perform a read-only independent review of Phase 2 in the existing WhatsApp
Tech Digest worktree. Read AGENTS.md, HANDOFF.md, README.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, especially
Phase 2. Inspect the complete diff and search the full production path for all
semantic word-list enforcement and all provider retry/repair entry points. Do
not edit files or invoke any runtime model or protected system.

Verify with concrete code paths and tests that lexical meaning no longer
changes production behavior; structural privacy/provenance/protected-token
checks remain; span validation preserves polarity without an English negator
list; titles cannot introduce protected facts; transport failure is terminal;
and only one closed-code validation repair is possible. Run focused tests, the
full suite, and git diff --check. Return APPROVE or REVISE with only concrete
blockers and file/line references.
```

---

## Phase 3 — Add bounded-prefix recovery and portable artifact binding

### Goal

Prevent a retained checkpoint from creating an indefinitely growing window,
and make an artifact reviewed from an isolated copy safely deliverable through
the unchanged live spool, without automatic omission or new semantic state.

Add an explicit operator-only cutoff selecting one contiguous prefix after the
current checkpoint and no later than the latest durable change. Bind the same
cutoff through snapshot selection, events, provenance, artifact identity,
delivery validation, and checkpoint advancement. Later rows stay pending. Mark
the artifact as a bounded historical backlog window. The scheduler must be
unable to choose this mode.

This phase does not run the mode. It implements and tests it offline.

The delivery-path amendment is mandatory, not a future Phase 6 discovery. The
current code writes the reviewed-artifact row into the isolated rendering copy,
while delivery searches one supplied spool for that row. Delivery through the
copy would advance only the copy; delivery through the live spool cannot find
the row. Phase 3 must add one tested application path for a portable owner-only
review envelope that the live spool independently validates before SMTP.

The private envelope carries the existing revision-only delivery facts needed
for verification: checkpoint/cutoff, artifact and policy hashes, run/model and
count metadata, canonical provenance and its hash, and coverage metadata. It
must not contain message text, recipient, credentials, or SMTP data. Because
provenance contains immutable source identities, the envelope and its parent
directory remain owner-only and outside Git.

The envelope's own hashes are unkeyed, so they establish integrity against
corruption, not authenticity. Every fact the envelope asserts that is not
re-derivable from the artifact bytes, the live policy, or live spool data must
therefore be independently recomputed from the live spool before it can reach
SMTP or the durable record. Treat the envelope as a claim to be checked, never
as evidence in its own right.

### Execution prompt

**Recommended session model: GPT-5.6 Sol, High reasoning.**

```text
Continue the existing WhatsApp Tech Digest worktree. This is a fresh session.
First read AGENTS.md, HANDOFF.md, README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, then follow
only Phase 3. Inspect git status and the complete existing diff; preserve all
existing work and do not reset, revert, clean, commit, or create another
worktree. Confirm that Phases 1 and 2 have accepted reviews; if not, stop.

Implement Phase 3 test-first. Add an explicit operator-only bounded cutoff for
a contiguous pending prefix. Validate it before model construction and carry
the exact same cutoff through snapshot/event selection, provenance, artifact
identity, delivery checks, and checkpoint advancement. On successful delivery
advance only to that cutoff; leave all later changes pending. Label the result
as a bounded historical backlog window, and require human review for possible
later corrections or supersession. Prove the scheduler cannot select or infer
this mode.

Also replace the currently broken isolated-review-to-live-delivery handoff with
a portable owner-only review envelope. Render-only on an isolated consistent
copy must write the exact artifact plus an envelope containing the existing
revision-only delivery facts, not message content or delivery secrets. A
delivery attempt against the live spool must verify the artifact/envelope
hashes and schema, policy and target binding, unchanged checkpoint and clear
delivery state, exact cutoff, and every provenance revision against live data.
It must also recompute, rather than trust, every envelope fact that live data
can establish, including the window's source and candidate counts, its run
type, and the coverage recorded with the run. It may register or consume the
verified envelope through a tested application API, but must never require a
model rerun, a render against production, or manual database-row copying. It
must reject stale, forged, changed, wrong-policy, wrong-target, revoked, or
mismatched evidence before SMTP, and must reject replay of a snapshot that was
already accepted or left unresolved. Verify a bounded window against the prefix
state at its cutoff: a later edit to an in-prefix source must stay pending
rather than block the reviewed window, while a revoked source must fail at
render time and again before SMTP. A transport failure must leave the same
reviewed artifact retryable without a rerender. Advance only the live
checkpoint, and only after acceptance.

Do not invoke a model, access a protected spool or policy, render an artifact,
send SMTP, change services, or change scheduling. Do not add semantic caching,
automatic batching, or automatic cutoff selection.

Cover invalid/old/future cutoffs, empty prefixes, later-row retention,
provenance mismatch, artifact mismatch, delivery mismatch, exact checkpoint
advance, rollback/failure, historical labeling, and scheduler denial. Add
isolated-copy/live-spool tests for altered or absent envelopes, weak file mode,
wrong target/policy/checkpoint/cutoff, missing or changed provenance revisions,
revocation before and after the render, a self-consistent envelope whose
unkeyed hashes were recomputed after its counts or run type were rewritten,
replay after acceptance and after an unresolved outcome, retry after a
transport failure, delivery of a bounded window whose in-prefix source was
later edited, live-only checkpoint advance after acceptance, and proof that no
model or production render occurs.
Run focused tests, the full unit suite, and git diff --check. Stop with the
standard sanitized phase report. Do not start Phase 4.
```

### Success gate

- One explicit cutoff defines the same contiguous prefix at every boundary.
- No later row is consumed, omitted, or discarded.
- Failure cannot advance the checkpoint.
- Scheduler and unattended paths cannot choose the recovery mode.
- An isolated render's exact bytes can be verified and delivered through the
  live spool without rerendering or copying database rows manually.
- No envelope-asserted fact that live data can establish is trusted; a
  self-consistent rewritten envelope fails before SMTP.
- A bounded window whose in-prefix source was later edited stays deliverable,
  and that later edit stays pending; a revoked source always fails closed.
- A mismatch or stale envelope fails before SMTP, replay of an accepted or
  unresolved snapshot is refused, and a transport failure leaves the same
  artifact retryable; successful acceptance advances only the live checkpoint
  to the reviewed cutoff.
- Full tests and `git diff --check` pass.

### Independent review prompt

**Recommended reviewer: Claude Opus, High/extended thinking.**

```text
Perform a read-only independent review of Phase 3 in the existing WhatsApp
Tech Digest worktree. Read AGENTS.md, HANDOFF.md, README.md,
DEPLOYMENT_GUIDE.md, RUNBOOK.md, DIGEST_REDESIGN.md, and
REDESIGN_IMPLEMENTATION_PLAN.md in full, especially Phase 3. Inspect the full
diff and trace one cutoff end-to-end through selection, provenance, portable
review-envelope creation, live-spool verification, delivery checks, and
checkpoint transition. Do not edit, invoke a model, access protected runtime
data, send SMTP, or change services.

Try to find any path that selects a noncontiguous window, consumes later rows,
advances beyond the cutoff, loses later corrections, mislabels the artifact,
lets a scheduled/unattended run choose the cutoff, trusts an isolated database
row or an envelope-asserted fact in place of live verification, requires a
production render, or advances only the isolated copy. Confirm the current
isolated-to-live delivery gap is actually closed before any provider call, and
that a bounded window remains deliverable when an in-prefix source was later
edited. Run focused tests, the full suite, and git diff --check. Return APPROVE
or REVISE with only concrete blockers and file/line references.
```

---

## Phase 4 — Accept the source and qualify on synthetic data

### Goal

Establish that the complete source diff is safe, then make one bounded
provider-backed measurement before touching realistic data.

The operator's use of the execution prompt below is explicit approval for at
most one initial synthetic digest runtime call and, only after a genuine
validation rejection, one targeted repair call. It also approves read-only use
of the protected policy solely to resolve the already configured adapter and
model, without displaying any protected value. It is not approval for
protected-spool access, SMTP, policy changes, service changes, or another
attempt after transport failure.

### Execution prompt

**Recommended session model: GPT-5.6 Terra, High reasoning.** The digest
runtime model must also remain the existing policy-pinned GPT-5.6 Terra/High.

```text
Continue the existing WhatsApp Tech Digest worktree. This is a fresh session.
First read AGENTS.md, HANDOFF.md, README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, then follow
only Phase 4. Inspect git status and the complete existing diff; preserve all
existing work.

First perform a final read-only review of the complete source diff and run the
full unit suite plus git diff --check. If either fails, do not invoke Hermes.

If source acceptance passes, perform exactly one synthetic large-window
qualification using the current compact one-call path and the existing
policy-pinned Hermes GPT-5.6 Terra/High adapter. Use publication-safe synthetic
fixtures only. Permit at most one targeted repair only after an actual
validation rejection with an allowed closed failure code. On timeout, stale
termination, child exit, empty output, or output-budget failure, do not retry.

Do not access any production or protected spool, print or retain prompt/output
content in shared output, send SMTP, change a checkpoint, modify policy, change
timeouts, restart services, or change the scheduler. Keep detailed evidence
owner-only and mode 0600 outside Git. Capture only sanitized component bytes,
available wrapper/input/output/reasoning token aggregates, first-event/total
timing, application-call count, internal-attempt count when safely available,
validation category, and synthetic required-atom results.

Pass only if the candidate completes within the existing agreed budget,
validates, reaches 100% on all non-excepted required atoms, has no unwaivable
quality/privacy failure, and is prepared for operator source-to-output review
against the synthetic sources. Do not claim the manual review passed on the
operator's behalf. An atom exception is valid only if it already has the owner
decision and independent review required by DIGEST_REDESIGN.md. Preserve
evidence and stop with the standard sanitized report and the private review
artifact location. Do not start Phase 5 or implement the two-call branch in
this session.
```

### Success gate

- Complete source diff accepted; full tests and diff check pass.
- Synthetic call completes and validates within the existing budget.
- Required-atom, fabrication, privacy, resolution, and supersession gates pass.
- The operator records PASS after manually comparing synthetic sources with
  the private artifact.
- The compact one-call design becomes **eligible**, not yet selected.

Any failure routes to Conditional Phase C. It does not authorize timeout or
provider-configuration changes.

### Phase 4 outcome

The 2026-09-20 synthetic qualification completed and validated in one
application call within the configured budget and retained all 19 required
atoms, with no privacy or supersession failure. It did not pass: review found
one unresolved-placement error and one known unsupported synthetic sentinel.
The sanitized verdict is **NOT ELIGIBLE — REQUIRE TWO-CALL**. The operator then
directed Conditional Phase C. No operator manual PASS was recorded for the
one-call artifact.

### Phase 4 two-call repeat

Run this repeat only after Conditional Phase C has an accepted independent
review recorded in the acceptance table. The operator's use of the execution
prompt below is explicit approval for one extraction call and one
reconciliation call on the publication-safe synthetic large window, plus at
most one targeted repair after a genuine validation rejection at either
boundary. The maximum is three application calls total. A transport failure at
either stage is terminal and authorizes no further call. This approval permits
read-only use of the protected policy solely to resolve the already configured
adapter and model; it does not permit a policy write or protected-spool access.

```text
Repeat Phase 4 with the accepted Conditional Phase C two-call candidate.
First read the required public project documents, inspect the complete diff,
and confirm the acceptance table records an independent APPROVE for Phase C.
If it does not, stop without accessing protected configuration or invoking
Hermes. Run the full unit suite and git diff --check; stop if either fails.

Using only publication-safe synthetic fixtures, run exactly one extraction and
one reconciliation through the existing policy-pinned Hermes GPT-5.6
Terra/High adapter. Permit one additional application call only after a genuine
local validation rejection with an allowed closed failure code. Never retry a
timeout, stale termination, child exit, empty output, prompt/output-budget
failure, or other transport failure. Do not change policy, timeout, services,
scheduler, checkpoint, or SMTP state, and do not access any protected spool.

Keep prompts, responses, and the review artifact owner-only outside Git with
mode 0600. Report only sanitized extraction/reconciliation component bytes,
available token and timing aggregates, application/internal-attempt counts,
validation category, and required-atom/fabrication/privacy/resolution/
supersession results. Stop for operator source-to-output review. Do not claim
manual PASS, start Phase 5, or deliver.
```

The repeat passes only when both stages validate within the existing budget,
all non-excepted required atoms reach the deterministic rendering, and every
fabrication, privacy, resolution, and supersession gate passes. The operator
must then record manual artifact PASS and Phase 4 acceptance before Phase 5.

### Phase 4 two-call repeat outcome

The 2026-09-20 source gate passed: the complete candidate diff was accepted,
all 297 offline unit tests passed, and both staged and unstaged
`git diff --check` passed. The approved 87-source publication-safe synthetic
repeat then stopped during extraction after exactly one application call.
Hermes exited with a transport-class `child_exit` (status 1) after 251,933 ms;
the wrapper captured 110 stdout bytes and 244 stderr bytes privately. No
extraction candidate was accepted, Call B was not made, and no validation
repair or transport retry occurred.

The extraction request measured 817 instruction bytes, 860 schema bytes,
11,020 projected-source bytes, and 12,891 total provider-facing bytes. Since
there was no validated extraction, reconciliation bytes, atom recall, and the
fabrication/privacy/resolution/supersession output gates are not assessable.
Token and internal-attempt aggregates were unavailable. Detailed evidence and
the synthetic source-to-output review files remain owner-only outside Git.
The sanitized result is **NOT ELIGIBLE — TRANSPORT FAILURE**. No operator
manual artifact PASS is possible for this attempt, and Phase 5 must not start.

Subsequent read-only diagnosis found a Hermes v0.21.3 client-side watchdog
defect rather than an application prompt-budget or parent-timeout failure. The
small request used high reasoning effort, but Hermes selected prompt-size-only
silence fuses and killed three internal attempts after 90, 90, and 60 seconds;
the socket `ReadError` followed those deliberate closes. Upstream issue
`#112909` fixed this with a 300-second implicit silence floor for high-or-above
Codex reasoning plus a follow-up preserving the overall run-budget cap
(`a6cad512a5`, `ebd106f3ec`). Those two changes are locally backported with a
failing-then-passing no-network regression and pinned on the local Hermes
branch at `1e1c0a9c9e`. This remediation does not alter the recorded verdict,
qualify the candidate, or authorize another model call.

### Phase 4 post-repair repeat outcome

The operator then explicitly approved one further isolated repeat. Its source
gate again passed all 297 offline tests and both staged and unstaged diff
checks. The protected policy remained read-only and supplied only the existing
Hermes adapter, model, effort, and budget settings; the candidate pipeline was
selected explicitly for the synthetic evaluation without a policy write.

The repaired client completed Call A in 97,778 ms. Its 12,891 provider-facing
bytes comprised the same 817 instruction, 11,020 projected-source, and 860
schema bytes, and local extraction validation accepted 24 grounded atoms.
Call B completed in 125,793 ms with 10,315 provider-facing bytes but failed the
local `DISPOSITION` invariant because a reader-facing atom was not accounted as
`INCLUDE` or `UNCERTAIN`. The one permitted closed-code reconciliation repair
completed in 110,071 ms with 10,604 provider-facing bytes and failed the same
invariant. Total elapsed time was 333,647 ms across exactly three application
calls. No transport retry occurred; token and internal-attempt aggregates were
unavailable.

Because neither reconciliation candidate validated, no deterministic rendering
was accepted and required-atom, fabrication, privacy, resolution, and
supersession output gates are not assessable. No manual artifact PASS is
possible. The sanitized result is **NOT ELIGIBLE — VALIDATION FAILURE**. The
watchdog transport defect is fixed, but the two-call candidate remains
unqualified and Phase 5 must not start. Detailed evidence remains owner-only
outside Git.

Read-only comparison of both rejected plans found the same concrete mismatch:
each rendered 22 atoms while accounting six of those reader-facing atoms as
`CONTEXT`. The prompt had stated only that every `INCLUDE` or `UNCERTAIN` atom
must be rendered, while the validator correctly enforced the reciprocal rule
as well. A failing-then-passing regression now protects explicit instructions
that every reader-facing atom must be `INCLUDE` or `UNCERTAIN`, `CONTEXT` is
non-rendered only, and `EXCLUDE` cannot appear in a topic or unanswered entry.
This source-only fix
does not alter the failed verdict or authorize another model call.

Independent read-only review returned **APPROVE SOURCE FIX** after verifying the
Hermes backport against both upstream commits, the prompt/validator accounting
equivalence, repair bounds, boundary safety, fail-first regressions, public
documentation, 298 digest tests, and 102 focused no-network Hermes tests. The
operator accepted that reviewed source fix on 2026-09-21. This acceptance does
not qualify Phase 4 or authorize a further model call.

The reviewer also made a non-blocking observation that source-wide ownership
was enforced locally but not stated in the reconciliation instructions. Before
another qualification attempt, the operator directed that residual mismatch
to be closed. A fail-first prompt-contract assertion now passes after adding an
explicit rule that all atoms from one source belong to at most one topic or
unanswered entry. Focused independent review returned **APPROVE OWNERSHIP
DELTA**, and the operator accepted it on 2026-09-21. The source/test delta is
committed at `9bbec2d`; the full 298-test suite and staged/unstaged diff checks
pass. It made no model or operational call and does not alter the failed Phase
4 verdict. A final Phase 4 qualification gate is prepared but requires separate
explicit model-run approval.

### Independent review prompt

**Recommended reviewer: Claude Sonnet, High/extended thinking.**

```text
Perform a read-only independent review of the Phase 4 source and sanitized
measurement report. Read AGENTS.md, DIGEST_REDESIGN.md, and
REDESIGN_IMPLEMENTATION_PLAN.md in full. Inspect the complete source diff and
tests. Do not access private prompts, responses, artifacts, logs, identities,
credentials, paths, policies, or spools, and do not invoke another model.

Verify that the allowed application-call count was respected, no transport
failure was retried, evidence fields are privacy-safe, the synthetic rubric was
applied exactly, any atom exception was explicitly authorized and reviewable,
and the operator's manual-review decision is recorded. Do not infer that
decision yourself. Verify the result supports only ELIGIBLE or NOT ELIGIBLE.
Run the full unit suite and git diff --check. Return APPROVE ELIGIBLE, APPROVE
NOT ELIGIBLE, or REVISE with concrete blockers.
```

---

## Phase 5 — Three isolated realistic-window trials

### Goal

Decide the architecture on representative data rather than one lucky run.

The operator's use of the execution prompt below is explicit approval to read
the protected policy and production spool only for read-only preflight, three
fresh owner-only SQLite-consistent isolated copies, and the selected
candidate's normal application calls per frozen window. One targeted
validation repair per window is allowed; transport retries are not. This is a
maximum of six calls for one-pass or nine for two-call. It is not delivery
approval.

Window selection is a separate no-runtime-model setup step. Before the first
digest call, the operator must approve and freeze an owner-only manifest
containing exactly three contiguous window definitions and their explicit
cutoff values. The execution session may consume that manifest but must not
choose, alter, or replace a window. This preserves the operator-only cutoff
rule and prevents selection after results are known.

### Window-freeze setup prompt

**Recommended session model: GPT-5.6 Terra, High reasoning.** This step makes
no digest runtime call.

```text
Prepare the no-runtime-model Phase 5 window manifest for the existing WhatsApp
Tech Digest worktree. This is a fresh session. Read AGENTS.md, HANDOFF.md,
README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md, DIGEST_REDESIGN.md, and
REDESIGN_IMPLEMENTATION_PLAN.md in full, especially Phase 5. Confirm Phases
1-4 are accepted. Perform read-only protected preflight without printing any
protected value.

Propose exactly three realistic contiguous windows and explicit cutoff values,
using the current checkpoint and durable boundaries. Do not invoke Hermes or a
digest model, render, contact SMTP, change policy/checkpoint/services/scheduling,
or create the three spool copies yet. Write the proposal to one owner-only
private manifest outside Git with mode 0600; do not print its values. Report
only that three definitions were recorded and where the operator can review
the private manifest locally. Stop for operator approval.
```

The operator reviews the private definitions, adjusts them if desired, records
explicit approval, and freezes the manifest before starting the execution
prompt. Here, frozen means the operator approves the manifest's content hash;
the file remains owner-only mode 0600, and the execution session must verify
that exact hash before every call without rewriting the file. Record only the
sanitized facts `window_count=3`, `approved_hash_matched=true`, and
`frozen_before_first_call=true`; never publish the hash or cutoff values.

### Execution prompt

**Recommended session model: Claude Opus, High/extended thinking.** The digest
runtime model remains the policy-pinned GPT-5.6 Terra/High.

```text
Continue the existing WhatsApp Tech Digest worktree. This is a fresh session.
First read AGENTS.md, HANDOFF.md, README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, then follow
only Phase 5. Inspect git status and the complete diff; preserve all existing
work. Confirm Phase 4 was independently approved as ELIGIBLE. If not, stop.

Perform a read-only protected preflight without printing any protected value.
Require the already existing operator-approved, frozen, owner-only Phase 5
window manifest. If it is absent, unapproved, its content hash differs from the
operator-approved hash, it contains anything other than exactly three valid
contiguous window definitions, or approval was not recorded before this
session's first digest call, stop without invoking Hermes. Reverify the hash
before every call and do not choose or alter its windows. Create exactly three
fresh owner-only SQLite-consistent isolated spool copies matching those
definitions; do not mutate production. Keep all copies, artifacts, manifests,
and metadata outside Git, owner-only, mode 0600.

Run the accepted Phase 4 candidate through the existing policy-pinned Hermes
GPT-5.6 Terra/High adapter. A one-pass window normally makes one call; a
two-call window normally makes one extraction and one reconciliation call. A
window may make one additional call only for a genuine validator rejection
using an allowed closed repair code. Never retry a timeout, stale termination,
child exit, empty output, or output-budget failure. Every attempted window
counts; do not replace a failed or unfavorable window.

Do not contact SMTP, advance or edit the production checkpoint, change policy
or timeout settings, restart services, alter the scheduler, or clean up
evidence. Do not print content. For each successful artifact, preserve a
private review bundle for the operator. Do not claim a human quality decision
on the operator's behalf.

Return the standard sanitized report with each deterministic result,
application-call count, timing/token aggregates when available, and private
review artifact locations. If all three deterministic runs pass, report
AWAITING OPERATOR REVIEW. A failed one-call run reports REQUIRE TWO-CALL; a
failed two-call run reports CANDIDATE NOT ACCEPTED. Stop. Do not deliver or
implement another architecture in this session.
Once you finish, update documentation, if REDESIGN_IMPLEMENTATION_PLAN.md needs changes in this or future phases, update it.
```

### Success gate

- Three fixed windows were attempted and all three count.
- Their exact cutoffs were operator-approved and frozen in the private manifest
  before the first digest call; the execution session did not choose or alter
  them.
- Production data and checkpoint remained unchanged; SMTP was not contacted.
- Each artifact passes deterministic validation.
- The operator manually compares every source with its artifact for material
  technical coverage, unsupported facts, unanswered/resolved placement,
  limitations, corrections, supersession, privacy, and usefulness, and records
  PASS for each window.
- Only three clean passes select the accepted Phase 4 candidate architecture.

### Independent review prompt

**Recommended reviewer: GPT-5.6 Sol, High reasoning.**

```text
Perform an independent read-only review of the Phase 5 procedure and results.
Read AGENTS.md, DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full.
Inspect the complete source diff and sanitized report. Do not inspect, ingest,
reproduce, quote, print, move, or modify private messages, artifacts, spools,
prompts, responses, or metadata. The operator's recorded manual review is the
quality decision; do not replace it with an AI content review. Do not invoke a
runtime model, contact SMTP, touch the production checkpoint, or change
services/configuration.

Verify all three predetermined windows count, calls stayed within policy,
transport failures were not retried, each artifact is source-complete and
unsupported-content-free according to the recorded operator review, privacy
and resolution gates were applied, and production state did not change. Verify
from sanitized timing/attestation evidence that exactly three
operator-approved cutoffs were frozen before the first digest call and were not
altered or replaced (`window_count=3`, `approved_hash_matched=true`, and
`frozen_before_first_call=true`). Return APPROVE SELECT ONE-CALL, APPROVE
SELECT TWO-CALL, APPROVE REQUIRE TWO-CALL (only for a failed one-call
candidate), or REVISE with only sanitized concrete blockers. This review
is not artifact acceptance and not delivery approval.

```

---

## Conditional Phase C — Implement the ephemeral two-call path

Run this only if Phase 4 or Phase 5 concludes that the compact one-call path is
not eligible. A provider outage alone should first be reported and assessed;
it is not proof that task decomposition is needed.

### Goal

Implement exactly the approved ephemeral extraction/reconciliation design,
without a cache, ledger, automatic batching, local semantic classifier, or new
credential path.

Call A returns one disposition per source and exact grounded evidence atoms.
Call B receives only validated atoms plus opaque relationships, chronology, and
tracked state, and returns a structural plan. Deterministic rendering inserts
the validated atoms. Commands, versions, identifiers, paths, and URLs remain
locally attached structural values rather than model semantic roles.

### Execution prompt

**Recommended session model: Claude Opus, High/extended thinking.**

```text
Continue the existing WhatsApp Tech Digest worktree. This is a fresh session.
First read AGENTS.md, HANDOFF.md, README.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full, then follow
only Conditional Phase C. Inspect git status and the complete diff; preserve
all existing work. Confirm an accepted Phase 4 or Phase 5 verdict explicitly
requires the two-call candidate. If not, stop.

Implement the ephemeral two-call extractor/reconciler test-first, exactly as
specified in DIGEST_REDESIGN.md. Extraction sees the complete projected window
and necessary opaque reply context and returns one disposition per source plus
complete grounded evidence atoms. Validate every atom before reconciliation.
Reconciliation sees only validated atoms and necessary structural context and
returns a structural organization/resolution plan. Render factual prose only
from validated atoms. Keep commands, versions, identifiers, paths, and URLs as
locally attached structural values.

Persist no semantic cache or ledger. Add no Ollama preclassifier, embedding
clustering, direct-provider credential, Hermes internal API, automatic
batching, or lexical semantic gate. Preserve the existing privacy, provenance,
open-item, artifact, delivery, and checkpoint contracts. Normal execution is
two application calls; at most one closed-code validation repair is allowed;
transport failure is terminal.

Do not invoke any model or access protected runtime state in this phase. Run
focused tests, the full suite, and git diff --check. Stop with the standard
sanitized report; do not qualify or deliver.
```

### Success gate and review

All extraction and reconciliation fields have exact local accounting;
invalid atoms never reach Call B; factual rendering cannot invent prose; no new
semantic persistence exists; and full tests/diff check pass.

### Phase C implementation status

The 2026-09-20 source implementation and review remediation passed 297 offline
unit tests and both staged and unstaged `git diff --check`. It added no semantic
persistence and made no model, protected-spool, SMTP, service, or scheduler
call. Overbroad
read-only documentation searches matched non-secret pipeline-mode lines in the
ignored protected policy and owner-only handoff; nothing was modified.
Therefore the source success gate was met, but that session was not recorded as
a clean procedural PASS. The candidate remained unqualified and unselected.

The first independent review returned `REVISE` with four blockers. The current
tree remediates them by binding candidate count to every normalized source,
enforcing source-wide topic/unanswered ownership, requiring `tracked_item` for
the edited-`UPDATE` self-resolution exception, and excluding the policy-sourced
final instruction from reconciliation and its closed-code repair. Fifteen
focused Phase C tests pass. Independent re-review and operator acceptance are
complete: the re-review returned `APPROVE`, and the operator accepted Phase C
on 2026-09-20. The subsequent Phase 4 repeats are recorded above; the candidate
is not qualified or selected.

### Independent review prompt

**Recommended reviewer: GPT-5.6 Sol, High reasoning.**

```text
Perform a read-only independent review of Conditional Phase C in the existing
WhatsApp Tech Digest worktree. Read AGENTS.md, HANDOFF.md, README.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full. Inspect the
complete diff, both model-boundary projections, validators, deterministic
renderer, failure handling, and tests. Do not edit files, invoke a runtime
model, access protected runtime data, send SMTP, or change services.

Trace source accounting through extraction, pre-reconciliation validation,
reconciliation, deterministic rendering, provenance, and artifact creation.
Verify invalid atoms cannot reach Call B, final factual prose can only use
validated atoms, protected technical tokens remain local structural values,
normal/max call counts are two/three, transport failure is terminal, and no
cache, ledger, lexical semantic gate, or other deferred component was added.
Run focused tests, the full suite, and git diff --check. Return APPROVE or
REVISE with only concrete blockers and file/line references.
```

After approval, repeat Phases 4 and 5 with the candidate set to **two-call**.
Expected runtime use is two calls per clean window and a maximum of three only
after one actual validation rejection. The same no-transport-retry,
operator-manual-review, and all-windows-count rules apply.

---

## Phase 6 — Approve and deliver one exact artifact

### Goal

Deliver only bytes the operator has reviewed and explicitly approved, without
rerendering or invoking a model.

Phase 3 must already provide the independently reviewed and tested portable
review envelope that binds the exact artifact produced from an isolated
SQLite-consistent copy to the unchanged live checkpoint, policy hash,
immutable source revisions, cutoff, and delivery state. Phase 6 revalidates
that path and the specific artifact; it is not where the missing mechanism is
first discovered or designed.

Artifact quality approval and delivery authorization remain two distinct user
actions. The execution prompt below is used only after the operator has named
and approved the exact artifact and then separately approved its delivery.

### Pre-delivery read-only review prompt

**Recommended reviewer: Claude Opus, High/extended thinking.**

```text
Perform the Phase 6 read-only pre-delivery verification for the exact artifact
the operator identified. Read AGENTS.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md,
DIGEST_REDESIGN.md, and REDESIGN_IMPLEMENTATION_PLAN.md in full. Do not invoke a
model, rerender, send SMTP, modify any database/checkpoint/policy, restart a
service, or change scheduling.

Verify the Phase 3 portable-envelope path still binds these exact reviewed
bytes from the isolated consistent copy to the current unchanged live
checkpoint, policy hash, immutable source revisions, cutoff, and delivery
state, and that the window facts the envelope asserts still match the live
spool. Verify the approved artifact and envelope are still byte/hash identical,
owner-only, and not stale. If any link is absent or uncertain, return BLOCKED
with the sanitized category; do not improvise a transfer, rerender, or copy
database rows manually. Otherwise return READY FOR SEPARATE DELIVERY APPROVAL.
This is not delivery approval.
```

### Delivery execution prompt

**Recommended session model: GPT-5.6 Sol, High reasoning.**

```text
The exact reviewed artifact identified by the operator has passed Phase 6
pre-delivery verification, and the operator separately approves delivery of
those exact bytes now. Continue in the existing WhatsApp Tech Digest worktree.
Read AGENTS.md, DEPLOYMENT_GUIDE.md, RUNBOOK.md, DIGEST_REDESIGN.md, and Phase 6
of REDESIGN_IMPLEMENTATION_PLAN.md. Perform a fresh read-only preflight without
printing protected values.

Do not invoke a model or rerender. Reverify artifact hash/bytes, portable
envelope schema/hash, policy hash, immutable source revisions, exact cutoff,
current checkpoint, target binding, recipient binding, unresolved delivery
state, and the live-recomputed source count, candidate count, and run type for
the window. If any check differs, stop before SMTP and report only the
sanitized mismatch category.

If every check matches, make exactly one SMTP delivery attempt for the approved
bytes. Never retry automatically. A transport failure may be retried only after
the operator separately approves a further attempt for the same exact bytes; an
unresolved outcome must go to reconciliation instead. Advance the checkpoint
only after confirmed SMTP acceptance and only to the artifact's bound cutoff.
Preserve owner-only evidence. Do not restart services, change policy, alter
scheduling, clean up evidence, or deliver any later window. Report only
sanitized delivery category, SMTP-attempt count, and checkpoint-transition
category.
```

### Success gate

- Zero model calls and no rerender.
- The delivered bytes equal the approved artifact exactly.
- Exactly one SMTP attempt per delivery approval, with no automatic retry.
- Checkpoint advances only after acceptance and only to the bound cutoff.
- Any uncertainty stops before delivery or checkpoint change.

## Completion definition

The redesign is complete only when:

1. Phases 1-3 have accepted source reviews;
2. the winning architecture passes its Phase 4 and all three Phase 5 trials;
3. the operator has reviewed and accepted one exact artifact;
4. Phase 3 proves the isolated-artifact/live-checkpoint binding is supported,
   and Phase 6 revalidates it for the exact artifact;
5. separately approved delivery succeeds or fails closed; and
6. the public handoff is updated with sanitized reusable facts only.

Scheduling or unattended activation is a later, separate operating-policy
decision. It is not part of this redesign implementation plan.
