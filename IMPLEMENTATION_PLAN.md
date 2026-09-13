# Implementation Plan and Evidence Map

## Purpose

This document records the public, source-level implementation plan for WhatsApp Tech Digest. It is not a record of live deployment status and must not contain operational identities, credentials, recipient details, runtime paths, message content, or private evidence.

## Product objective

Produce a compact technical digest from one explicitly approved WhatsApp group while preserving high-value announcements, technical discussions, answered questions, and unanswered technical questions/issues. The result is a selective engineering brief, not a transcript.

The digest is still valuable when only unanswered technical questions exist. An all-empty model candidate is currently rejected when active source revisions exist, so a no-material source window remains pending rather than silently advancing the checkpoint. A future operational phase must define a deterministic, reviewable no-material checkpoint contract before unattended use.

## Architecture plan

### 1. Collector boundary

- Ingest only from one policy-pinned immutable group identity.
- Treat every inbound message as untrusted data.
- Persist only through the durable local spool.
- Return a skip directive to prevent normal conversational-agent handling.
- Deny all WhatsApp output primitives.

### 2. Normalization and privacy boundary

- Deduplicate immutable message revisions and handle revocations safely.
- Normalize text and redact secret-shaped assignments before model projection.
- Keep participant identifiers, group identity, recipient identity, and runtime metadata local.
- Use safe contact labels only when provider metadata supplies them; never fabricate names from identifiers.

### 3. Selective summarization

- Support a two-stage local path and a one-pass Hermes Codex path behind explicit policy selection.
- Keep complete relevant thread context in the selected pipeline.
- Use model semantic classification for question and unresolved-issue selection, then independently require the cited source and any selected question excerpt to be a technical question/request. Require interrogative punctuation for a bare wh- opening so relative and exclamative clauses are not read as questions. Require an unresolved technical problem signal for `ISSUE` and reject explicit resolution language.
- Require model-cited source references for reader-facing questions and unresolved issues.

### 4. Local validation and rendering

- Validate dispositions, source ownership, source overlap, exact source excerpts, protected values, attribution, and confidence.
- Require every reader-facing question, narrative, recommendation, action, and limitation to be one contiguous, word-bounded normalized, privacy-sanitized exact source excerpt. Reject excerpts that drop an immediately preceding negator. Require exact token membership for protected technical identifiers; substring containment is insufficient.
- Require a semantic `QUESTION` source kind for `Question asked` output.
- Permit unanswered entries only for a semantic `QUESTION` or `ISSUE` source that remains `INCLUDE` or `UNCERTAIN`.
- Keep unanswered questions/issues in a dedicated section capped at eight items.
- Render actionable but limited troubleshooting guidance as an update with a limitation.
- Permit unanswered-only windows, keep model topic labels internal, and derive unanswered status text locally.
- Omit empty fields and use one shared sanitizer to prevent supported WhatsApp mention, participant/group/web JID, newsletter, and broadcast identifier forms from reaching prompts or reader-facing output.

### 5. Reviewed-artifact delivery

- Keep render-only work isolated and non-delivering.
- Bind acceptance to an artifact hash, protected-policy hash, immutable source revisions, and checkpoint.
- Deliver reviewed bytes directly only after separate explicit delivery authorization.
- Fail closed on artifact, policy, source-revision, checkpoint, validation, or delivery uncertainty.

## Evidence expectations

Every material source change should include:

1. a regression test that fails before the fix;
2. focused test output after the fix;
3. full-suite and whitespace-check output;
4. an isolated render-only replay where appropriate;
5. independent review for logic, privacy, or delivery-boundary changes.

Operational evidence remains owner-only and outside Git. Public evidence must be sanitized and limited to test/review outcomes, never source messages or runtime identities.

## Current source-level milestones

- Collector-only ingestion and outbound-deny guard.
- Durable spool with immutable-revision and checkpoint safety.
- Safe model projection/redaction boundary.
- Selective topic rendering with source-grounded answered and unanswered content.
- Model-cited semantic question/issue selection rather than lexical-only detection.
- Deterministic technical-question/issue checks and exact-source-excerpt grounding for reader-facing one-pass prose.
- Reviewed-artifact delivery boundary and explicit execution modes.
- Publication-safe one-pass quality corpus, deterministic scorer, bounded call accounting, owner-only evidence writer, and validated config/provider model construction.

## Remaining phases

1. **Accuracy/privacy hardening accepted (complete).** The independent review was accepted and committed as `0.2.0`; Phase 2 does not reopen it without a concrete regression.
2. **Complete the isolated one-pass quality evaluation.** The initial approved smoke test completed 16 runs with 19 Hermes calls and did not meet the gates. Deterministic remediation now covers the exposed `api_token` projection regression, corrects the limited-guidance corpus expectation, and rejects silent omission of source-authored status changes. A focused rerun and human unsupported-content review remain separately approval-gated. Keep detailed artifacts owner-only and publish only sanitized aggregate findings. Treat no-material results as input to a later checkpoint design, not validation of checkpoint behavior. See [`ONE_PASS_EVALUATION_SPEC.md`](ONE_PASS_EVALUATION_SPEC.md) and [`ONE_PASS_EVALUATION_FINDINGS.md`](ONE_PASS_EVALUATION_FINDINGS.md).
3. **Add cross-day resolution state.** Carry validated unanswered items forward by revision-only identity so later source-backed answers can move them from open to partial or resolved without replaying unrelated history.
4. **Operational acceptance.** Repeat protected configuration preflight and exact-artifact review. Delivery and scheduling remain separate explicit approvals; they are not part of source acceptance.

## Future changes

Any future work involving delivery, scheduling, bridge activation, provider changes, target changes, or infrastructure must begin with a read-only preflight and obtain the approval required by [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) and [`RUNBOOK.md`](RUNBOOK.md).

## Related documentation

- [`README.md`](README.md) — overall architecture and source safety invariant.
- [`HANDOFF.md`](HANDOFF.md) — sanitized handoff facts.
- [`POLICY_CONTRACT.md`](POLICY_CONTRACT.md) — configuration field contract.
- [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) — staged deployment boundaries.
- [`RUNBOOK.md`](RUNBOOK.md) — verification and incident response.
