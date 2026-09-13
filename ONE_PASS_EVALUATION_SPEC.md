# One-Pass Hermes Quality Evaluation Specification

**Status:** Phase 2 infrastructure and an initial approved 16-run Hermes smoke test are complete. The initial run did not meet the quality gates; deterministic remediation is implemented and focused reruns remain separately approval-gated. See [`ONE_PASS_EVALUATION_FINDINGS.md`](ONE_PASS_EVALUATION_FINDINGS.md). This specification does not authorize additional model invocation, spool access, `render-only`, SMTP, protected-policy/runtime access, service changes, or scheduling.

## Objective

Perform a small, isolated quality evaluation of the working one-pass prompt, schema, validator, and renderer. The evaluation targets manager-critical accuracy and privacy failures rather than broad stylistic preferences.

This is separate from the historical two-stage Ollama work in [`EVALUATION_SPEC.md`](EVALUATION_SPEC.md). Any earlier Ollama preauthorization has no applicability to Hermes.

## Corpus

[`tests/fixtures/one_pass_quality_corpus.json`](tests/fixtures/one_pass_quality_corpus.json) contains eight fully synthetic public windows. All people, systems, identifiers, URLs, commands, and events are invented.

| Window | Primary coverage |
| --- | --- |
| Administrative assignment | Important announcement, assignment, deadline, priority allocation, and volunteer path |
| Answered question | Source question, answer, command, product identifier, and public `.invalid` reference |
| Open and limited guidance | One unresolved question and one normal update containing actionable guidance plus a source-backed limitation |
| Correction chain | Correction and final advice superseding an earlier path |
| Uncertain field guidance | Useful observation with explicit uncertainty and documentation limit |
| Identifier privacy | Synthetic mention, device JID, `lid`, group JID, broadcast handle, and secret-shaped value |
| Fabricated migration topic | Regression for the prior invented migration/workshop topic |
| All nonmaterial | Greetings, acknowledgements, social content, and non-actionable speculation |

Each material window declares two atom classes:

- `required_atoms` are manager-critical facts. Missing any required atom fails the quality gate.
- `optional_atoms` are useful but nonessential. Their recall is reported separately and is not a Phase 2 gate.

Resolution expectations use only the current schema:

- answered questions belong to normal topics;
- actionable but incomplete guidance belongs to a normal topic with a source-backed `limitation`;
- only questions or issues without reusable guidance belong in `unanswered`.

## Run count and interpretation

Evaluate each window twice: **16 evaluation runs**, requiring **16–32 Hermes model invocations** depending on repair/fallback use. This is an exploratory repeatability smoke test, not a statistical reliability estimate. The thresholds below are Phase 2 acceptance gates only.

## Metrics and gates

| Metric | Phase 2 gate |
| --- | --- |
| Manager-critical required-atom omission rate | `0%` |
| Optional-atom recall | Report only |
| Unsupported accepted content | `0` |
| Answered/update-with-limitation/unanswered classification errors | `0` |
| Privacy leaks in any projected prompt or accepted output | `0` |
| Supersession error count | `0` |
| Material-window final failure rate | `0%` |
| Material-window validation retry rate | At most `2/14` runs (`<=15%`) |
| Accepted fabricated output from no-material windows | `0` |

The deterministic scorer checks declared atoms, expected resolution placement, privacy sentinels, known fabricated terms, and superseded advice. Exact-source validation covers substantive reader prose. A human reviewer must additionally inspect every accepted artifact for unsupported factual titles or content before model quality can be accepted.

No-material runs measure whether the model fabricates an update or the current path rejects it. They do not exercise or validate checkpoint behavior because the evaluator never opens a spool or runner execution mode. Findings are input to a later fail-closed no-material checkpoint design.

## Infrastructure boundary

The evaluator in `whatsapp_tech_digest.quality_eval`:

- applies the same deterministic one-pass normalization and pre-model filtering as the runner;
- calls the existing `summarize_actionable` prompt/schema/validation/rendering path;
- constructs models only through `DigestConfig.from_dict()` and `build_model()`;
- requires an `example_only` one-pass Hermes policy using the local Hermes CLI connector;
- requires exactly two runs per window;
- counts projected-prompt privacy leaks on rejected runs as well as accepted ones;
- refuses to place detailed evidence inside the repository;
- creates a new owner-only artifact directory and exclusive mode-`0600` evidence files;
- emits a sanitized aggregate containing counts and rates only.

The publication-safe model configuration is [`config/one_pass_evaluation.policy.example.json`](config/one_pass_evaluation.policy.example.json). It contains no live target, recipient, credential, state path, or runtime identity. The `--invoke-model` switch prevents accidental execution but is not approval by itself.

## Artifact boundary

Commit only:

- the sanitized synthetic corpus and expected atoms;
- evaluator source and deterministic tests;
- aggregate counts/rates and sanitized conclusions after review.

Keep model prompts, raw responses, rendered digests, validation details, and run logs owner-only and outside Git. Preserve them until explicit cleanup approval.

## Controlled execution sequence

1. Accept the evaluation infrastructure independently through source review and deterministic tests.
2. Obtain exact approval for the 16 synthetic runs and possible 32 Hermes invocations.
3. Create a new owner-only artifact directory outside the repository.
4. Execute only the synthetic corpus through the example-only validated configuration path.
5. Review every accepted prompt/response/digest artifact for unsupported factual content.
6. Produce a sanitized aggregate report and apply the metric gates.
7. Add a failing regression before any digest behavior change exposed by the findings.
8. Update public documentation only with sanitized aggregate findings.

The model command is intentionally not an authorization:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m whatsapp_tech_digest.quality_eval \
  --corpus tests/fixtures/one_pass_quality_corpus.json \
  --policy config/one_pass_evaluation.policy.example.json \
  --artifact-dir /OWNER_ONLY_PATH_OUTSIDE_GIT/one-pass-quality-evidence \
  --invoke-model
```

It must not be run until the operator approves that exact Hermes action.

After a remediation, `--window` may be repeated to run only explicitly approved corpus windows. A focused rerun remains a new model action and requires its own exact approval.

## Separate acceptance decisions

1. **Evaluation infrastructure accepted:** corpus safety, expected atoms, scoring, call accounting, model construction, and artifact isolation are correct.
2. **Model quality accepted:** the approved synthetic runs meet every blocking gate after human review.

A model-quality miss produces findings and regression coverage. It does not invalidate correct `0.2.1` evaluation infrastructure. The initial run exposed validated digest/privacy behavior changes, so the remediated source is versioned `0.3.0`.
