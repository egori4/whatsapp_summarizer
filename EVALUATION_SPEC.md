# Classification and Digest Quality Evaluation Specification

**Status:** Stage 1 specification approved and Stage 2 offline implementation approved in direct chat on 2026-09-03. This document does not authorize WhatsApp collection, gateway/plugin changes, restart, email, scheduling, or production-spool access.

This document is the historical two-stage Ollama evaluation specification. Its Stage 3 preauthorization applies only to the stated loopback Ollama fixtures and does **not** authorize Hermes model calls. The separate one-pass Phase 2 design is in [`ONE_PASS_EVALUATION_SPEC.md`](ONE_PASS_EVALUATION_SPEC.md).

## Objective

Evaluate the offline classification and grounded-digest path against mixed group-style traffic: actionable technical and operational updates, ordinary chatter, technically worded non-actions, corrections, and adversarial input. The policy remains **high recall / conservative exclusion**: preserve potentially material updates rather than silently omitting them.

## Data boundary

Fixtures are local JSON under `tests/fixtures/evaluation/` and contain no raw WhatsApp transcript, group JID, phone number, participant/handle, external URL, credential, or production identifier.

Most fixtures are invented. `01_chatter_heavy_semantic_transform.json` is a fully de-identified, semantically transformed derivative of a user-provided discussion: it retains only generalized testing semantics. It does not retain source wording, names, numbers, links, product names, commands, or other identifiers. The evaluator rejects unsafe identity fields, URL-like text, phone-number-like text, and WhatsApp identifier shapes.

## Scenario packs

1. Chatter-heavy semantic transformation: troubleshooting, migration questions, reusable-template discussion, mundane acknowledgements, and several confirmed technical actions.
2. Baseline technical day: one confirmed release update amid greetings.
3. Technical noise: technically worded questions and speculation with no conclusion.
4. Incident lifecycle: detection, mitigation context, and confirmed current resolution.
5. Revision/correction: superseded status must not be retained as current.
6. Adversarial/redaction: injection and secret-shaped input must not influence output.
7. Borderline policy: uncertain customer-impact risk is retained under high-recall policy.

## Fixture and evaluation contract

Each event has a non-real immutable `(message_id, change_seq)` and one `digest_role`: `required`, `optional_context`, `exclude`, `adversarial`, or `superseded`.

Each scenario supplies deterministic classifier rows, required source references, required verbatim fact atoms, and a structured grounded final response. The evaluator executes:

```text
fixture -> stage_zero -> StaticModel preclassifier -> high_recall_select/context
-> StaticModel final response -> render_grounded -> report
```

The deterministic path exercises real pipeline logic without an Ollama request or any other transport.

## Blocking conditions

- Any missing required source or fact atom.
- A raw classification false-positive for `exclude`, `adversarial`, or `superseded` content.
- A final claim that fails immutable source grounding.
- A stale/retracted update presented as current.
- Injection-following, unsafe fixture shape, schema failure, or degraded result represented as success.

Chatter introduced only through intentional context expansion is reported separately from raw classifier false positives; it is not silently treated as a classifier miss.

## Controlled next stages

1. **Stage 2 (approved):** deterministic fixtures, evaluator, tests, offline verification, and artifact evidence in a pinned disposable checkout.
2. **Artifact review gate:** inspect the exact implementation and evidence before accepting it.
3. **Stage 3 (pre-authorized, after artifact acceptance):** invoke only the existing policy-pinned loopback Ollama models against these fixtures, three runs per scenario, with no WhatsApp, SMTP, gateway, scheduler, or external-network operation.
4. **Stage 4:** retain model-quality evidence for operator review. Any activation remains a separate explicit approval.
