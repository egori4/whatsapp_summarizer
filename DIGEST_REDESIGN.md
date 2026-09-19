# Simplified Digest Redesign Proposal

## Status and purpose

This document is a design proposal for independent review and implementation
planning. It does not authorize a model call, production-spool access, SMTP,
policy changes, service changes, or delivery.

The goal is deliberately narrow: reliably create an accurate, useful technical
digest from one WhatsApp group without wasting model tokens or using local word
lists to guess meaning.

This revision removes the proposed durable evidence ledger and cache. For a
once-daily personal digest, saving one extraction call after an occasional
failure does not justify cache migrations, invalidation rules, production cache
warming, or making extraction blind to conversational context.

## Decision summary

Use the smallest design that passes measured quality and runtime gates:

1. First compact and harden the existing one-call pipeline.
2. Measure it once on synthetic data and once, with separate approval, on an
   isolated realistic window.
3. If the compacted one-call pipeline is timely and accurate, stop there.
4. Only if it still fails, split it into two ephemeral model calls:
   evidence extraction followed by reconciliation.
5. Persist no new semantic cache between runs. Existing durable message,
   checkpoint, reviewed-artifact, and open-question state remain unchanged.

This gives two possible final architectures:

```text
Preferred if it passes

project/redact -> one compact model call -> validate/render -> review/delivery
```

```text
Fallback only if measurement requires it

project/redact -> extract evidence -> reconcile evidence -> deterministic render
```

## Confirmed problem with the current design

Sanitized observations from recent runs:

- One retained window contained 87 distinct projected sources. A later attempt
  selected 90 sources from 91 pending messages.
- The 87-source structured prompt was approximately 35.1 KB:
  - instructions: approximately 7.4 KB;
  - projected sources: approximately 13.8 KB;
  - appended JSON schema: approximately 14.0 KB.
- Actual projected source text was only approximately 5.0 KB.
- A source-free Hermes/Terra probe completed quickly.
- A synthetic structured prompt of approximately 51.7 KB completed in about 15
  seconds with approximately 10.2K reported input tokens.
- Real compound digest calls did not return a usable assistant response.
- Raising the outer run budget to 300 seconds did not resolve the failure.
  Hermes ended provider waits through its internal no-first-response/stale-call
  watchdog.
- Hermes retried internally, after which the application invoked the same
  configured model again as a fallback.
- SMTP was not reached and the checkpoint remained unchanged, so messages were
  retained correctly.

The evidence does not prove whether the missing first response was caused by
provider queueing, hidden reasoning, streaming behavior, or the compound task.
It does show avoidable application problems:

1. the response schema repeats source-reference enums many times;
2. the model must classify, reconcile, resolve, quote, organize, and render all
   sources in one response;
3. transport retry and validation repair are conflated;
4. identical final and fallback configurations can duplicate provider work
   after a Hermes child exit;
5. the one-pass path still contains local English lexical rules that can
   override semantic model decisions; and
6. failed checkpoints accumulate a harder window for the next attempt.

## Core accuracy boundary

### The model decides meaning

The model decides whether a source is:

- material, supporting context, irrelevant, or uncertain;
- a question, issue, answer, update, action, limitation, correction, or status
  change;
- resolved, partially resolved, superseded, or unanswered; and
- documented, confirmed, or informal field guidance.

Local code must not infer these meanings from English words, phrases, or
regular expressions.

### Local code verifies facts and structure

Deterministic code continues to verify:

- exact source-reference coverage, membership, and uniqueness;
- normalized exact source excerpts;
- sentence/source boundaries and unique span resolution;
- exact protected commands, versions, identifiers, paths, and URLs;
- privacy redaction and prohibited identifiers;
- structural reply/quote relationships;
- distinct answer evidence before resolving a question or issue;
- topic/unanswered non-overlap;
- immutable source revisions;
- reviewed-artifact hashes, SMTP state, and checkpoint transitions.

### No lexical semantic gates

The new production path must not use vocabulary matching to include, exclude,
classify, resolve, or assign confidence. This includes the current semantic
uses of:

- `_ACKS` and `_ACK_ONLY`;
- `_POLICY_OVERRIDE`;
- `_LOW_CONFIDENCE`; and
- `_STATUS_CHANGE` / `announces_status_change`.

Even an inclusion-only lexical rule is not neutral: a false positive can force
irrelevant content or reject an otherwise valid digest. These rules may remain
temporarily as non-authoritative evaluation metrics, but they must not change a
production candidate, validation result, delivery decision, or checkpoint.

Implementation must remove the enforcement behavior, not merely rename or
delete regex constants. The current enforcement sites include:

- acknowledgement and policy-override exclusion during `stage_zero` and
  one-pass source selection;
- the status-change rejection in `render_actionable`; and
- both low-confidence rejection paths in `render_actionable`.

Regression tests must prove that none of these paths can include, exclude, or
reject a production candidate based on vocabulary.

Structural regular expressions remain permitted for syntax rather than
meaning, including secret redaction, WhatsApp/JID forms, participant
identifiers, URLs, protected technical tokens, and opaque reference syntax.

Removing the lexical policy-override filter is a security-posture change. The
replacement controls are structural: source content is always untrusted data;
the model receives no delivery authority or useful tools; output must satisfy a
fixed schema; every reference and reader-facing fact is locally validated; and
SMTP remains behind the reviewed-artifact boundary.

## Step 1: compact and harden the existing one-call path

This is the cheapest possible repair and must be measured before building the
two-call fallback.

### Compact the provider-facing schema

Replace the per-source disposition object with a list of rows such as:

```json
{
  "dispositions": [
    {"source_ref": "S001", "value": "INCLUDE"}
  ],
  "topics": [],
  "unanswered": []
}
```

The schema uses ordinary string references rather than repeating the complete
source enum at every reference field. Local validation already has the supplied
reference set and must require exact coverage, reject unknown or duplicate
references, and validate every nested reference.

Use `$defs`/`$ref` only where it measurably reduces the serialized schema and is
accepted reliably by the model. List-form references plus local membership
validation provide most of the benefit without depending on model resolution
of a complex schema.

The serialized schema and full prompt must be measured before and after this
change. No claim that compaction fixes the provider failure is valid until a
controlled model measurement succeeds.

### Simplify the instruction contract

The current instruction block is approximately 7.4 KB and expresses many rules
as dense prose. It is both a token cost and a plausible contributor to long
pre-response reasoning.

Rewrite it as a concise, numbered contract. Preserve only decisions the model
must make, including semantic dispositions, topic grouping, question/answer
resolution, corrections, limitations, actions, uncertainty, and concise output.
Remove prose that merely restates deterministic validation, including repeated
exact-copy, protected-token, lexical-confidence, and formatting explanations.

The shorter instruction must not weaken the validator. Measure instruction
bytes separately from schema, projected-source, and total prompt bytes. The
synthetic quality corpus determines whether simplification removed information
the model actually needed.

### Separate transport failure from validation repair

The retry policy becomes explicit:

- timeout, stale provider termination, child exit, empty response, or output
  budget failure: stop; never invoke an identical configured model again;
- schema or grounding rejection after a response: optionally allow one
  targeted repair using a closed, content-free failure-code enum;
- a real fallback exists only when its provider/model identity is different and
  explicitly configured;
- never put `str(exception)`, rejected output, provider stderr, or source text
  into a repair instruction.

Normal one-call execution therefore uses one application model call. If a
targeted validation repair is enabled, the hard maximum is two calls. Transport
failure never triggers the second call.

### Replace the negator list with structural span safety

Every reader-facing factual atom must be either:

- one complete mechanically delimited normalized sentence; or
- the complete normalized source when a reliable sentence cannot be selected.

The returned text must resolve to one unique source span. Mid-sentence,
ambiguous, or ungrounded spans are rejected. Local code must not silently
extend or shorten the model's excerpt.

This prevents rendering a polarity-stripped fragment such as an action cut out
of a negative instruction without maintaining an English negator vocabulary.

### Preserve useful generated titles

Readable titles are important to a digest. The model may generate a short
topic title. Validation requires:

- bounded length, one line, and no raw source references or privacy
  placeholders;
- no version, command, URL, path, date, numeral, or protected identifier absent
  from that topic's validated evidence atoms.

An exact suitable title atom is preferred but not mandatory. A title is an
organizational aid; all factual body text remains exact source-grounded
evidence.

### Add operator-only bounded backlog recovery

A retained checkpoint must not create an unbounded liveness failure. Add an
operator-selectable cutoff that renders a contiguous prefix strictly after the
current checkpoint and no later than the latest durable change.

Requirements:

- this mode is operator-only and never selected automatically by the scheduler;
- the chosen cutoff is explicit and validated before model construction;
- snapshot, pending-event selection, provenance, reviewed-artifact identity,
  delivery, and checkpoint advancement all bind to that same cutoff;
- successful delivery advances only to the chosen cutoff;
- later durable changes remain pending and are neither omitted nor discarded;
- the artifact is clearly labeled as a bounded historical backlog window so it
  is not mistaken for the latest complete state;
- render-only human review must consider the risk that a later pending source
  corrects or supersedes advice inside the prefix; and
- no prefix artifact is delivered without the existing exact-artifact approval.

This is a recovery mechanism, not the normal schedule. It provides a controlled
way to drain the present backlog in smaller reviewed digests even if a full
window remains too difficult. It adds no new durable semantic state.

### Preserve exact-artifact delivery across the isolated-spool boundary

The current reviewed-artifact row is local to the spool used for rendering.
An artifact rendered against an isolated copy therefore cannot be delivered
through the live spool merely by presenting its short manifest: the live spool
does not contain that review row. Delivering through the isolated copy would
advance only the copy's checkpoint. This path must be repaired before any
provider-backed qualification.

Render-only must produce an owner-only portable review envelope, alongside the
exact artifact, containing the existing delivery facts needed for independent
live verification: checkpoint and cutoff, content and policy hashes, run/model
metadata, canonical provenance and its hash, and coverage/count metadata. The
envelope remains private because provenance contains immutable source
identities. It contains no message text, recipient, credential, or SMTP data.

Delivery against the live spool must, without invoking a model or rerendering:

- verify the exact artifact and envelope hashes and schema;
- verify the same target through the live policy/spool binding;
- require an unchanged live checkpoint and clear delivery state;
- validate the cutoff and every immutable provenance revision against the live
  spool, including carried-question coverage and later revocation/supersession;
- reject stale, forged, altered, replayed, or mismatched envelopes before SMTP;
- register or consume the reviewed envelope through a tested application path,
  never by manually copying database rows; and
- record delivery and advance only the live checkpoint, only after SMTP
  acceptance, and only to the reviewed cutoff.

This is a portability repair for the existing reviewed-artifact boundary, not
a semantic cache or a second delivery mechanism. Its source path and negative
tests must pass independent review before synthetic or realistic model calls.

## Decision gate after Step 1

Measure:

1. serialized schema and prompt bytes on the existing synthetic large-window
   fixture;
2. sanitized Hermes per-call wrapper/input/output/reasoning tokens when
   available;
3. time to first provider event and total duration;
4. output-validation result; and
5. required-atom recall and reader quality.

Model calls require separate approval. Start with synthetic data. Then perform
at most one compacted-monolith render-only replay on a fresh owner-only copy of
a realistic spool window.

If the compacted monolith returns within the agreed budget, validates, and
passes human source-to-output review, it becomes eligible for the remaining
realistic-window trials. One successful run does not select the final
architecture.

If it still times out, terminates stale, or fails the quality gate, preserve the
measurement and implement Step 2. One model result is an operational benchmark,
not semantic ground truth.

Retain the one-call architecture only after it passes every attempted realistic
window in the later two-or-three-window gate. If any attempted window repeats
the provider or quality failure, implement Step 2 and repeat that gate with the
two-call candidate.

## Step 2, only if needed: ephemeral two-call pipeline

### Call A: evidence extraction

The extractor sees the complete projected window, including opaque structural
reply links and the bounded parent/reply context needed to interpret short
answers, confirmations, corrections, and follow-ups. There is no persistence
requirement, so accuracy takes priority over cache stability.

It returns one row for every source:

```json
{
  "sources": [
    {
      "source_ref": "S001",
      "disposition": "MATERIAL",
      "kind": "QUESTION",
      "evidence": [
        {"role": "QUESTION", "text": "complete exact source sentence"}
      ]
    }
  ]
}
```

The exact taxonomy should remain small. Expected dispositions are `MATERIAL`,
`SUPPORTING_CONTEXT`, `NOISE`, and `UNCERTAIN`. Expected evidence roles include
only what reconciliation and rendering require, such as `QUESTION`, `ISSUE`,
`ANSWER`, `UPDATE`, `ACTION`, `STATUS`, `GUIDANCE`, `LIMITATION`, `CORRECTION`,
and `REFERENCE`.

Commands, versions, identifiers, paths, and URLs are already recognized and
validated structurally. Local code attaches those protected values to their
source/atom; the model does not spend tokens relabeling them as semantic roles.

Every source must receive exactly one disposition. Every evidence string must
pass the complete-sentence-or-complete-source and unique-span rules before Call
B is permitted.

Nothing from Call A is persisted as a new semantic cache. A later failed run
may repeat extraction. That cost is accepted in exchange for simpler code and
full conversational context.

### Call B: reconciliation

The reconciler receives:

- opaque source and atom references;
- validated, redacted, exact evidence text;
- extractor-declared roles, dispositions, and uncertainty;
- reply/quote relationships, chronology, edit state, and tracked open-question
  state.

It does not receive irrelevant full sources, participant identifiers, group
identity, policy, SMTP data, credentials, runtime paths, or previous rejected
model output.

It returns a structural plan:

- topics and generated titles;
- evidence atoms assigned to situation, action, limitation, reference, and
  confidence roles;
- question/issue atom plus distinct answer atoms;
- `partial`, `resolved`, or unanswered status; and
- an explicit accounting decision for every retained atom.

It does not rewrite factual body prose.

### Deterministic rendering

The renderer substitutes the validated exact evidence atoms into the structural
plan. It derives attribution and URLs locally and applies the existing privacy,
provenance, open-question, reviewed-artifact, SMTP, and checkpoint gates.

The model may organize facts but cannot invent final factual sentences between
extraction and email rendering.

## Expected model calls

| Operating mode | Normal calls | Maximum automatic calls | Notes |
| --- | ---: | ---: | --- |
| Compacted one-call path | 1 | 2 | Second call only for a validation repair, never transport failure |
| Ephemeral two-call path | 2 | 3 | One extraction, one reconciliation, optional targeted validation repair |
| No pending or tracked work | 0 | 0 | No reason to invoke a model |

Hermes may perform internal provider attempts within one application call.
Diagnostics must distinguish known application calls from known internal
attempts without recording prompts, responses, identities, credentials, or
paths.

## What crosses the model boundary

### One-call path

Send only:

- concise digest instructions and compact schema;
- request-local opaque source references;
- redacted source text;
- opaque reply/quote links;
- timestamps where chronology matters;
- tracked-item and revision indicators.

### Two-call extraction

Send the same projected sources and conversational structure, but only the
short extraction instruction and extraction schema.

### Two-call reconciliation

Send only validated evidence atoms, opaque relationships, chronology, and
tracked state required to group and resolve them.

### Never send

- group JID or group display name;
- raw message IDs;
- participant identifiers or unredacted identity data;
- spool/runtime paths or sequence internals;
- policy, recipient, SMTP, credential, or session data;
- raw logs, provider stderr, or rejected model output.

## Rough token usage

These are application-visible estimates, not guarantees. They exclude unknown
Hermes/provider system framing, hidden reasoning, and internal retries.

The observed synthetic ratio was approximately 51.7 KB for 10.2K input tokens,
but JSON, URLs, commands, and source languages change tokenization.

| Design | Visible input estimate | Visible output estimate | Wrapper multiplicity |
| --- | ---: | ---: | ---: |
| Current 87-source monolith | approximately 7K-10K | approximately 2K-5K | 1 per application attempt |
| Compacted monolith target | approximately 5.5K-8K | approximately 1.5K-4K | 1 |
| Full-window extraction | approximately 3.5K-6K | approximately 1.2K-3K | 1 |
| Reconciliation | approximately 2K-4K | approximately 0.5K-1.5K | 1 |
| Two-call total | approximately 5.5K-10K | approximately 1.7K-4.5K | 2 |

The two-call pipeline is not expected to provide a dramatic first-run token
saving. Its benefit is smaller semantic tasks, earlier provider response,
validated intermediate evidence, and deterministic final prose. If the compacted
monolith works, it is the more token-efficient solution and should be kept.

## Quality and acceptance gates

### Deterministic tests

- list-form dispositions cover exactly the supplied source set;
- unknown, duplicate, or missing references fail closed;
- instruction, schema, source projection, and total prompt bytes are measured
  separately;
- complete-sentence-or-complete-source spans resolve uniquely;
- privacy and protected technical values remain exact;
- no lexical semantic rule affects production selection or validation;
- questions/issues require distinct answer evidence before resolution;
- tracked unanswered items remain carried correctly;
- transport failure cannot invoke an identical fallback;
- repair prompts accept only a closed failure-code enum;
- generated titles satisfy length, privacy, and protected-token checks;
- an explicit bounded cutoff selects only a contiguous pending prefix, binds
  provenance/artifact/checkpoint state to that cutoff, and leaves later changes
  pending;
- the scheduler cannot select bounded-prefix recovery automatically;
- artifact review, delivery, and checkpoint behavior remain unchanged.

### Synthetic model-quality gate

The existing annotated fixtures must retain 100% of required atoms, including
manager-critical updates, questions, issues, answers, commands, versions,
limitations, assignments, corrections, status changes, and URLs. No known
fabrication, privacy leak, false resolution, or superseded guidance is accepted.

Failure means the candidate design is not eligible for realistic replay. It
does not mean the gate may be silently reduced. A specific fixture atom may be
excepted only through a recorded owner decision plus independent review that
classifies it as an annotation defect, deliberate contract change, or accepted
noncritical quality tradeoff. The exception must identify that atom and its
rationale; it cannot lower a class-wide recall threshold. Manager-critical
facts, unresolved questions/issues, privacy, fabrication, false resolution,
and superseded-guidance failures are not waivable.

### Realistic-window gate

For two or three fresh isolated render-only windows:

- a human compares every source to extracted atoms and the final artifact;
- no material technical atom identified by human review is missing;
- no unsupported factual content is present;
- unanswered and resolved items are placed correctly; and
- reader usefulness and skimmability are acceptable.

Every attempted window counts, including provider or validation failures. The
one-call architecture is selected only if it passes all attempted windows. A
failed attempt cannot be replaced silently with a more favorable sample.

Run one compacted-monolith baseline on the same fixed window if it completes.
The candidate should have no human-confirmed recall regression against that
baseline. The baseline model output is a comparison point, not ground truth;
human source review remains authoritative.

## Minimal implementation and approval sequence

1. **Compact and harden offline.** Add regression tests, compact the monolith
   schema and instructions, separate retry/repair, remove production lexical
   semantic gates, replace the negator list with structural span validation,
   add operator-only bounded-prefix recovery, and add a tested portable
   reviewed-artifact envelope that the live spool can independently validate.
   Measure serialized bytes and run the full deterministic suite.
2. **Measure before splitting.** With explicit approval, measure sanitized
   wrapper usage on synthetic data and perform one isolated compacted-monolith
   render-only replay. A pass makes the one-call design eligible for Phase 3;
   it does not finalize the architecture. A failure triggers the ephemeral
   extraction/reconciliation split and repeated synthetic validation.
3. **Validate realistic output.** Run the selected design render-only against
   fresh owner-only spool copies covering two or three realistic windows and
   perform human source-to-output review. The one-call design must pass every
   attempted window or be replaced by the two-call candidate, which must repeat
   this gate. No SMTP or production checkpoint change occurs.
4. **Approve and deliver.** Approve one exact artifact, then separately approve
   delivery of those exact bytes. Existing artifact-hash, SMTP-state, and
   checkpoint rules remain authoritative.

Independent source review is required before the first model measurement.
Service or scheduler changes remain separate from artifact delivery when they
are actually needed; they are not additional redesign phases.

## Explicitly deferred

Do not implement unless real operation later demonstrates a material need:

- durable semantic/evidence caching;
- cache schema migrations or production cache warming;
- Ollama or another preclassifier;
- embedding-based clustering;
- a new direct-provider credential path;
- Hermes internal plugin APIs;
- automatic batch extraction.

If full-window extraction later proves too slow, add measured byte/source
batching as a focused optimization. Do not design it preemptively.

## Questions for final independent acceptance

1. Does the concise instruction-contract requirement preserve every semantic
   decision the model must make while removing validator duplication?
2. Is the operator-only contiguous-prefix recovery contract sufficient for
   liveness without permitting automated omission or misleading current-state
   output?
3. Is the narrow, atom-specific exception process proportionate without making
   manager-critical accuracy and safety gates waivable?
4. Are there any remaining implementation blockers, rather than optional future
   improvements?

The requested review outcome is one of:

- **approve for implementation planning**;
- **revise**, identifying only concrete remaining blockers; or
- **reject**, with a simpler alternative that preserves the stated accuracy and
  token objectives.

Approval remains design-only. It does not authorize implementation, model use,
production access, delivery, or service changes.
