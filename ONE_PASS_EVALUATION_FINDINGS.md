# One-Pass Phase 2 Evaluation Findings

## Status

The first approved synthetic Hermes smoke test completed. It did **not** meet the model-quality acceptance gates. The run exposed one corpus-labeling defect, one concrete privacy regression, and one manager-critical omission. Deterministic remediation is implemented, but focused model reruns remain separately approval-gated.

Detailed prompts, raw responses, rendered digests, validation details, and logs remain owner-only outside Git and are preserved pending explicit cleanup approval. This file contains aggregate counts and sanitized conclusions only.

## Initial pre-remediation aggregate

- Evaluation runs: `16/16`.
- Hermes model invocations: `19` of a possible `32`.
- Material runs: `14`.
- Material validation retries: `1/14` (`7.14%`), within the exploratory Phase 2 retry gate.
- Material final failures: `0/14`.
- All-nonmaterial outcomes: `2/2` failed closed; no fabricated digest was accepted.
- Required-atom recall under the initial annotations: `95%`.
- Optional-atom recall: `0%` (reported only; not a blocking gate).
- Resolution-placement errors: `1`.
- Privacy leaks into projected prompts: `2` occurrences across the two privacy runs.
- Known fabricated migration/workshop content accepted: `0`.
- Supersession errors: `0`.
- Human unsupported-content review: incomplete because automated blocking gates had already failed.
- Model-quality decision: **not accepted**.

These two repetitions are an exploratory repeatability check, not a statistical reliability estimate.

## Findings and remediation

### Secret-shaped projection regression

The shared redactor covered `api_key` and bare `token` assignments but missed the synthetic `api_token=...` form because the underscore prevented the bare-token boundary match. The value reached both privacy-run prompts.

Remediation:

- added a failing regression before the behavior change;
- extended the shared secret pattern to cover `api_token` alongside the existing forms;
- verified the focused regression and full deterministic suite.

This is a concrete Phase 1 privacy regression, so the narrow hardening change is in scope and requires version `0.3.0`.

### Privacy-window expectation defect

The original privacy issue combined unresolved status with a concrete log-gathering action, while the expected label required `unanswered`. That conflicted with the current contract: actionable guidance belongs in a normal update, with a source-backed limitation when applicable.

Remediation:

- separated the unresolved problem statement from the privacy-sensitive follow-up action;
- retained the problem as the expected unanswered item;
- moved the nonessential routing action to optional recall.

This changes the synthetic corpus, not runtime digest behavior.

### Manager-critical cancellation omission

One run retained a follow-up assignment but excluded the associated reliability-call cancellation. The other repetition retained both. The omitted cancellation is manager-critical and remains a valid model-quality failure.

Remediation:

- added a failing regression reproducing the accepted omission;
- added a deterministic recall net for declarative, unhedged source statements that something was scheduled, confirmed, moved, postponed, or cancelled;
- require a matching source to be included or uncertain and assigned to a topic or an unanswered entry, without dictating reader wording, so an over-inclusive match cannot deadlock the digest;
- replaced the earlier conjunction of technical, event, and status word lists, which both over-triggered on chatter and missed common phrasings such as `standup`, `pushed back`, and `no longer happening`;
- strengthened the one-pass instruction consistently.

## Remaining acceptance work

The remediation has deterministic coverage but has not been validated with additional Hermes calls. Model quality remains not accepted until a separately approved focused rerun covers the corrected privacy and fabricated-migration windows and every accepted artifact receives the unsupported-content review required by [`ONE_PASS_EVALUATION_SPEC.md`](ONE_PASS_EVALUATION_SPEC.md). The focused rerun is exactly four evaluation runs and can require four to eight additional Hermes calls.

No checkpoint behavior was tested or inferred from the all-nonmaterial outcomes.
