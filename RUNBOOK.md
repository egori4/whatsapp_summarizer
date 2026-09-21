# Operations Runbook

## Scope and safety boundary

This runbook is intentionally publication-safe. It provides safe source verification and decision points; it does not contain live target identifiers, recipients, credentials, local paths, service names, or claims about current runtime state.

The digest is a collector-only pipeline. It must never act as a conversational WhatsApp bot or emit WhatsApp output.

Read [`AGENTS.md`](AGENTS.md), [`HANDOFF.md`](HANDOFF.md), and [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) before any operational work.

## Approval matrix

| Activity | Approval required | Expected effect |
| --- | --- | --- |
| Read source, tests, and public documentation | No additional approval | None |
| Run unit tests and `git diff --check` | No additional approval | None |
| Create an isolated render-only preview | Explicit model-run approval | No SMTP, no production checkpoint change |
| Modify ignored policy or protected runtime configuration | Explicit configuration approval | Local protected-state change |
| Restart/activate a bridge, gateway, or service | Explicit operational approval | Runtime interruption/change possible |
| Send a reviewed artifact | Explicit delivery approval for that artifact | SMTP relay attempt only |
| Enable/change scheduling | Explicit activation approval | Future automated execution |

Never infer one approval from another.

## Safe source verification

From a review checkout, run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q
git diff --check
git status --short
```

Expected result: test suites pass, the diff is whitespace-clean, and no owner-only policy, runtime data, credential, spool, digest, log, backup, or session file is tracked.

## Read-only preflight checklist

Before a render, delivery, activation, restart, or scheduler change, confirm the following through read-only checks appropriate to the protected environment:

- the local policy is owner-only and ignored by Git;
- the exact approved group identity is consistently pinned across collector and gateway boundaries;
- bridge and model endpoints remain loopback-only;
- the collector remains deny-all for WhatsApp output routes;
- the durable spool is bound to the intended target and is not being reused for another target;
- no unresolved delivery state or checkpoint conflict exists;
- the planned execution mode is explicit.

Do not print or copy any resulting identity, message, recipient, credential, path, or runtime-state value into public artifacts.

## Execution-mode rules

- `validate-only` is the default and must not invoke a model, send SMTP, create a digest run, or advance a checkpoint.
- `render-only` may invoke the approved model only after explicit approval. It must use an isolated spool for preview work and must not contact SMTP or mutate production checkpoint state.
- Bounded-prefix recovery is operator-only. It requires an explicit durable cutoff plus owner-only artifact and portable-envelope output paths in `render-only`; the unattended wrapper and delivery-capable generation path cannot choose or infer it.
- Only the actionable `one_pass` and `two_call` pipelines may produce a deliverable candidate. Phase C source review is accepted. The first Phase 4 two-call repeat exposed a Hermes v0.21.3 high-effort small-prompt watchdog defect; the upstream effort-aware fix is locally backported. An approved post-repair repeat completed Call A, Call B, and the one allowed reconciliation repair, confirming the transport correction, but both reconciliation candidates failed the same local `DISPOSITION` invariant and no artifact was accepted. The reconciliation prompt now states the validator's reciprocal reader/accounting rule explicitly and a regression protects it; this source fix does not qualify the candidate. The result remains `NOT ELIGIBLE — VALIDATION FAILURE`, and another model run requires separate approval. The legacy two-stage pipeline is explicitly non-production and is refused for reviewed-artifact registration, `delivery-capable` runs, and reviewed-artifact delivery.
- `delivery-capable` can either send an exact reviewed artifact after live-spool portable-envelope validation, without model regeneration or production rendering, or execute the separately gated unattended generate-and-deliver path. The reviewed-artifact path is the default operational recommendation; unattended use requires its own explicit policy and activation approval.
- Keep the reviewed artifact and its envelope as regular owner-only `0600` files. Delivery refuses weaker permissions, and an SMTP transport failure leaves that exact artifact retryable, while an unresolved `unknown` outcome must go through reconciliation instead.

## Preview quality review

For each isolated rendered artifact, check:

- a bounded recovery artifact is visibly labeled as a historical backlog window, uses the operator-selected cutoff, and warns that later pending changes may correct or supersede it;

- important announcements and direct assignments are retained;
- technical discussions with reusable guidance are retained, with limitations when applicable;
- answered questions and conclusions use exact normalized, privacy-sanitized source excerpts;
- unanswered technical questions or issues have their own section;
- greetings, thanks, unrelated chatter, opaque identifiers, numeric sender labels, secrets, and empty headings are absent;
- no question is invented from an announcement or directive;
- every reader-facing question, summary, recommendation, action, and limitation is one complete sentence of a declared source, or that whole source;
- source negation, scope, and conditions survive because no clause was cut out of its sentence, and explicitly fixed/resolved issues are not listed as unanswered;
- generated titles are short, readable, and introduce no version, command, URL, path, identifier, or number that is not already in that topic's own text;
- internal unanswered topic labels are not rendered, and an unanswered-only window still produces a useful digest;
- a useful-but-limited workaround is an update with a limitation, not an unanswered item.
- a limitation is not automatically a partial answer: compare the answer with the actual scope of the tracked question or problem;
- a normal partial/resolved transition contains reader-facing evidence from a distinct answer source; only an edited tracked `UPDATE` may resolve from its own revision;
- edited tracked items remain represented by their current revision, while revoked items disappear from carry-forward and return to normal raw-retention handling;
- carried items do not expand the reader-facing date label beyond the current source window.

An unanswered-only window should render a digest. An all-empty candidate with active source revisions must remain pending; the current source does not authorize an unattended no-material checkpoint advance.

## Incident handling

### Model or validation failure

1. Do not send mail or advance the checkpoint.
2. Preserve the failure category and isolated evidence without copying message bodies or identities to public documentation.
3. A transport failure category (timeout, stale termination, child exit, empty output, prompt budget, output budget, CLI unavailable) is terminal for the run: make no further application call. Record the total already made; a one-pass initial failure is one call, while a two-call failure may occur after an earlier successful extraction or validation repair. Do not retry without diagnosing the transport cause.
4. Add a deterministic regression test before changing validation or rendering behavior.
5. Re-run unit tests, then use an isolated render-only replay.
6. Obtain independent review before an operational retry.

If the failure says a tracked revision was removed by deterministic normalization, do not edit the database or acknowledge the item away. Correct the source message so it is again a valid question, issue, or explicit edited resolution, or revoke it at the source if it should no longer be tracked; then retry only after the new revision has been collected. This recovery changes source state and does not itself authorize a model run, delivery, restart, or scheduling.

### Delivery uncertainty

1. Treat SMTP acceptance as relay evidence, not inbox confirmation.
2. Do not resend automatically.
3. Preserve the reviewed-artifact manifest and delivery state.
4. Reconcile through the protected operational channel before any retry.

### Target/configuration mismatch

1. Stop before collection, rendering, or delivery.
2. Do not reuse a spool for a different target.
3. Correct the protected local configuration only with explicit approval.
4. Repeat read-only preflight before resuming.

### Runtime exposure or output-route concern

1. Stop activation or delivery activity.
2. Confirm the collector remains output-denied and loopback-bound.
3. Preserve evidence and request explicit approval before any restart or configuration change.

## Evidence retention

Keep private operational evidence owner-only and outside Git: policy, runtime state, logs, spool data, rendered digests, review manifests, and rollback material. Public commits should retain only sanitized tests, source changes, and reusable conclusions.

## Related documentation

- [`README.md`](README.md) — system overview and source safety invariants.
- [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) — staged deployment gates.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — architecture and evidence map.
