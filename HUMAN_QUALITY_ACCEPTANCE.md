# Human-quality model acceptance review

**Scope:** isolated candidate worktree only. The review used the de-identified semantic fixture `tests/fixtures/operator_daily_brief_semantic.json` and loopback Ollama. It did not access WhatsApp, a spool, SMTP, the gateway, a scheduler, or external services.

## Review standard

The acceptance standard is a concise team brief, not merely valid JSON or a passing automated test. For each run, a reviewer must compare the input messages, classifier rows, structured final response, and rendered reader-facing digest.

Pass only when the digest:

1. groups distinct actionable topics into a scanable reader-facing brief;
2. retains source-supported status, limitation, risk, direct guidance, command/reference, and explicitly unresolved gap where present;
3. excludes acknowledgements, social chat, duplicate context, stale/retracted material, and attempted prompt injection;
4. does not upgrade a question or field guidance into confirmed documentation, a certainty, or a required action;
5. contains only validator-grounded, source-verbatim reader-facing bullets;
6. places a source-verbatim technical question/request in `Unanswered Technical Questions` only when no substantive source-backed answer or resolution appears in the supplied input window; and
7. has no classifier degradation or final-model fallback in the reviewed run.

## Representative real-model review

The source semantic structure covered five topics: virtualized configuration migration, bulk policy creation, portal migration limits, stale-neighbor behavior after replacement, and an upgrade-path failure. It also included acknowledgements and an injection-shaped message.

The first seven-topic final-model attempt completed with `qwen3.5:9b` and rendered five concise sections. It retained:

- the absence of a dedicated configuration-migration utility and the scoped manual guidance;
- template/export plus spreadsheet-based bulk-policy guidance;
- migration scope limits and the explicit historical-data gap;
- the replacement symptom, cached-neighbor inspection guidance, and gratuitous update command; and
- the failed intermediate upgrade path, supplied reference, and direct-target guidance.

It did not add reader-facing source IDs, chatter, or unsupported material. The rendered text is stored as a safe local evidence artifact at `/tmp/wtd-final-only-human-review-result.json` during this evaluation session.

**Human judgment:** accepted as materially similar to the requested technical-update style. It is intentionally more conservative than free-form prose: bullets are source-verbatim so that factual content cannot be silently paraphrased or invented. Section titles are dynamic, short, and non-factual.

## Remediation incorporated after review

- Added structured topic sections and deterministic renderer formatting rather than returning a flat citation list.
- Bound classifier explanation fields and instructed the classifier to treat policy/recipient/tool/output override attempts as `NOISE`, preventing a long injection rationale from consuming the response budget or becoming a candidate.
- Changed the sanitized policy example to small classifier batches and a model timeout compatible with the observed local-model runtime. This is not an activation of the protected policy.
- Retained strict source grounding; it was not relaxed to make a model failure look successful.

## Summary-contract review extension

A loopback-only `qwen3.5:4b` review of a de-identified question/answer/link slice was rendered through the current deterministic contract. The reader-facing result preserved the original linked question, its source-metadata asker, the exact answer/action, the unresolved request, and the exact repository URL. Safe evidence: `/tmp/wtd-summary-contract-human-review-result.json`.

## Unanswered technical-query extension

The isolated candidate now has a dedicated `Unanswered Technical Questions` output section. The classifier is instructed to retain material technical questions and requests, while the final model may emit a source-verbatim question only when its supplied context contains no substantive answer or resolution. The grounding validator requires an included technical-query source and rejects invented, excluded, or non-question entries; it also suppresses a duplicated question from a normal technical section so the reader sees it once only under the dedicated section.

The representative fixture now includes one unresolved technical utility request and one similar question with a source-backed later answer. A loopback-only `qwen3.5:4b` review of that three-message semantic slice passed: it retained the source-backed release answer, emitted only the unresolved request under `Unanswered Technical Questions`, and did not list the resolved question. The safe local evidence artifact is `/tmp/wtd-unanswered-questions-human-review-result.json`. The broader full-day review remains degraded because the 9B model exceeded its timeout and the 4B fallback was used.

## Remaining acceptance limit

This is one representative human-quality acceptance run, not a guarantee for every possible input or a production approval. The broader adversarial/revision scenario suite still requires repeated local-model review before production activation can be considered.
