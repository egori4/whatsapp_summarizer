# v2.6.0 — Weekly resolution analysis

## Decision

Add an opt-in hybrid weekly mode. The weekly analyzer reads every retained usable
raw message selected by the canonical daily checkpoint chain and uses validated
daily cards only as topic hints. It independently identifies questions, later
answers, and important findings omitted from daily digests. Only original message
IDs are accepted as evidence. Daily model prompts, checkpoints, recipients,
collection, timers, and the existing weekly period/delivery ledger are unchanged.

## Configuration and rollout

Within the existing workflow, set:

    weekly_summary:
      enabled: true
      evidence_mode: hybrid
      # retain the existing Monday schedule

Also set global retention.processed_raw_days to at least 14. The new sample config
uses 14 days and hybrid mode. Older configs without evidence_mode retain
daily_cards mode (v2.5), enabling safe rollout and rollback. No new timer, service,
schema migration, embeddings, issue database, agent, or model call is required.
For rollback set evidence_mode: daily_cards; do not disable daily processing.

## Evidence and output

- Preserve the previous Monday anchor, current Monday completion and contiguous
  checkpoint-chain coverage gates. For each selected daily artifact query raw
  messages in its exact change-sequence range. Compare the retained usable count
  against the recorded daily run count. On mismatch fail closed with source error
  before calling the model or sending a digest.
- Retention, deletion or edits that replace an old row may make an old weekly
  period unreplayable. The mutable raw store does not contain all historical
  revisions; do not silently replace old evidence or fall back to daily cards.
  Coverage means retained collected messages, not proof of bridge completeness.
- Include up to three days of preceding retained raw context, best effort.
  Historical-only issues cannot appear. Any resolved issue needs at least one
  current raw resolution source; a proposal, acknowledgement or silence is not
  confirmation. The weekly model uses one call; over-limit prompts fail explicitly
  rather than truncating source material.
- Two reader-facing sections (including explicit empty sections): Resolved Issues
  and Unresolved Issues, grouped by existing product family. Confirmed answers,
  useful findings, fixes and verified limitations include concrete resolution
  insight. The optional takeaway adds distinct operational guidance.
- Unresolved items carry proposed, partial or unanswered status, and explicit
  open questions. A lab success must not be presented as production confirmation.
  Nontechnical chatter is excluded. Team coordination appears only if materially
  relevant to an operational question. This is not a persistent issue tracker.
- Validate section/status combinations, raw citations, resolution-citation subsets,
  participants, URLs, current evidence and required open questions. Recheck raw
  snapshots after model generation. Store evidence IDs/dates in the existing
  weekly JSON. Structural checks cannot establish the semantic truth of a model
  claim; representative raw-message review is still required.
- Never attach raw messages to weekly emails. Existing production delivery
  and ledger semantics remain unchanged. Test recipients are always isolated.

## Validation and comparison

November 25, 2026: production v2.5 PR #3 merged into main, production switched
to that same code, raw retention increased to 14 days. v2.6 worktree remains
isolated. The previously delivered Nov 16–23 week had 103 usable messages;
only 41 remained in the old seven-day retention window (Nov 19–23). The live
comparison used separate private SQLite backups and the identical fully
retained suffix, labeled PARTIAL in both reports; no historical data fabricated.
The legacy and hybrid runs used the same configured GPT-6.2 high model. Both
produced five material topics, with three confirmed insights and two open
technical questions in hybrid mode. Manual review checked the raw evidence
for all five; the hybrid version retains a lab/production distinction and
does not claim independent CVE remediation just because a hotfix installed.
The initial hybrid pass omitted a useful named search reference and repeated
some resolution insights. The prompt was refined to retain useful named
references and make takeaways optional, avoiding duplicated guidance. The refined
real-data run completed successfully in 39.6 seconds (baseline: 33 seconds,
initial hybrid: 44.9 seconds); all five topics were retained and the useful
search reference restored. A final prompt refinement retained the precise
Cyrillic examples and avoided implying a permanent CVE fix; its live replay
completed in 45.1 seconds with the same five topics (3 resolved, 2 unresolved).
All resolution citations were manually checked against the raw replies. The
full-week missing-raw replay correctly refused before invoking the model.
Final full regression: 239 passed (27 added tests); diff check clean.
A single explicitly TEST/PARTIAL preview was accepted by SMTP with only the
operator in the envelope, no CC/BCC/raw attachment and no production ledger
change. Production email settings remain unchanged.
The comparison is a limited real-data sample, not proof of improved recall on
every week; deterministic fixtures cover missing-daily-summary topics and
cross-day confirmations, partial answers, and unanswered questions.

## Definition of done

- Full existing regression suite and v2.6 unit/integration tests pass.
- Both provider paths use the existing gateways; new schema supports native
  structured output, but the live quality comparison uses the configured
  Hermes model only.
- Old vs new live dry runs on the same available interval, with no production
  email, ledger modification, or checkpoint change. If sending a test preview,
  explicitly use the operator alone, with CC/BCC empty.
- Update version, README/config example/handoff, open feature PR, and verify
  checks. Enable hybrid mode in the production configuration only after
  reviewing the comparison. No image analysis in this release.


## Reader feedback refinements — November 25, 2026

Retain the two sections and existing detail level. A concrete, applicable technical
answer resolves a question without a subsequent acknowledgement or deployment test.
Do not claim a successful implementation or remediation unless reported. Reserve
`proposed` for genuinely tentative or inconclusive troubleshooting; label it
Suggested approach, with Unanswered and Partially answered for the other open states.
Compute resolved/unresolved issue counts from the validated JSON and display them
below the title in both text and HTML, including zero counts. No new model fields,
additional production model calls or daily workflow changes.

Validation: all 239 tests passed. The repeated real-data dry run on the same
41 retained messages found the same five issues (3 resolved, 2 unanswered)
in 40.0 seconds. A separate synthetic live-model check correctly classified
all four cases: concrete answer without feedback, partial answer, tentative
suggestion, and unanswered question. Counts were correct in both cases. A
revised test preview was accepted by SMTP with only the operator as recipient;
no CC/BCC or raw attachment. Production settings remain unchanged.
