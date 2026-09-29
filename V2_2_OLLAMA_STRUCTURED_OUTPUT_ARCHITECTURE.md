# WhatsApp Technical Digest v2.2 — Ollama Structured Output Architecture

**Status:** Proposed architecture / implementation plan
**Branch:** `v2.2-ollama-structured-output`
**Baseline:** merged v2.1 on `main` (`549e989`)
**Target package version:** `2.2.0`
**Primary goal:** make explicitly selected local Ollama generation structurally reliable without changing the existing Hermes/OpenAI path.

## 1. Problem statement

v2.1 can already call a named local Ollama provider through Hermes. In live testing with `qwen3.5:9b`, the model showed useful technical comprehension but unreliable machine-output behavior:

- it initially returned Markdown instead of JSON;
- after stronger prompting it returned JSON-shaped output but closed the top-level object early;
- it invented section IDs;
- it used the wrong type for `references`;
- it omitted required `source_ids`;
- it created unconfigured sections.

The content quality was promising enough to continue evaluating the model. The immediate engineering problem is therefore not to redesign summarization, but to make the Ollama transport enforce the existing digest output shape deterministically.

Ollama supports native JSON-Schema-constrained structured output through `POST /api/chat` using the `format` field. Ollama also supports disabling thinking with `think: false`, and recommends `temperature: 0` for deterministic structured output.
## 2. Scope

v2.2 adds exactly one new production capability:

> When a workflow explicitly selects `provider: ollama-local`, the digest uses a dedicated native Ollama structured-output path.

All other model/provider selections continue through the existing v2.1 Hermes `AIAgent` path.

In scope:

- exact routing for explicit `ollama-local`;
- native Ollama `/api/chat`;
- JSON Schema generated from the workflow's configured digest structure;
- `stream: false`, `think: false`, `temperature: 0`;
- current prompt, historical-context behavior and source records;
- current semantic/source validator;
- current timeout, debug capture, delivery and checkpoint semantics;
- automated tests and a small real-message quality benchmark.

Out of scope:

- generic OpenAI-compatible local-server framework;
- LM Studio, vLLM, llama.cpp or arbitrary custom provider support;
- automatic model fallback;
- automatic repair/retry model calls;
- multi-call classification or summarization;
- embeddings, persistent AI memory or topic databases;
- provider auto-detection;
- changing the existing Hermes generation behavior.
## 3. Non-negotiable design rules

### 3.1 Existing flow remains unchanged

For `provider: inherit`, `openai-codex`, and every provider other than the exact configured key `ollama-local`, v2.2 MUST execute the existing `HermesModelGateway` path without changing its request construction, Hermes `AIAgent` arguments, fallback setting, parsing or validation behavior.

The only common-path change permitted is a small gateway-selection step before model invocation.

### 3.2 Ollama selection is explicit

The new path activates only when trusted workflow configuration contains:

```yaml
model:
  provider: ollama-local
  name: <concrete Ollama model>
  reasoning: none
```

`provider: inherit` MUST NOT switch to the native Ollama path even if the globally inherited Hermes provider happens to be Ollama.

The Ollama path MUST NOT silently fall back to Hermes/OpenAI if local generation fails.

### 3.3 No lexical message classification

v2.2 MUST NOT classify, suppress, prioritize or route messages using hard-coded keyword/phrase matching.
Forbidden examples include application logic such as:

```python
if "hi" in text.lower(): ...
if text.lower() in {"thanks", "thank you", "bye"}: ...
if any(word in text for word in TECHNICAL_WORDS): ...
```

No greeting list, acknowledgement list, technical-keyword list, regex intent classifier, or similar lexical heuristic may determine whether a message is technically important.

The deterministic pre-model filter remains limited to structural facts already present in v2.1, such as configured group membership, deletion state and non-empty usable text/caption.

Relevance, acknowledgement/noise handling, topic interpretation and technical meaning remain semantic model responsibilities inside the one summarization call, followed by deterministic source/schema validation.

### 3.4 One generation call per run

The Ollama path performs one generation request.

There is no automatic second call for:

- classification;
- repair;
- validation feedback;
- retry with a different prompt;
- cloud fallback.

If generation or validation fails, the run fails and the checkpoint remains unchanged.

### 3.5 Existing validator remains authoritative

Ollama JSON Schema constrains output structure. It does not replace `validate_digest()`.
The current validator continues to enforce semantic and security properties that JSON Schema cannot safely establish, including:

- every cited source ID belongs to the model-visible source set;
- every item cites at least one current message;
- participant attribution refers to known participants;
- cited URLs appear in cited source text;
- configured section restrictions;
- application length/count limits.

No source-truth logic is moved into prompt wording.

## 4. High-level architecture

### 4.1 Existing v2.1 path

```text
runner
  -> HermesModelGateway
  -> private request JSON
  -> hermes_worker.py in Hermes Python environment
  -> Hermes runtime provider resolution
  -> AIAgent.chat()
  -> ModelInvocationResult
  -> validate_digest()
  -> render / dry-run or delivery / checkpoint
```

### 4.2 New v2.2 Ollama path

```text
runner
  -> configured model gateway router
       |
       +-- provider != ollama-local
       |     -> existing HermesModelGateway (unchanged)
       |
       +-- provider == ollama-local
             -> OllamaStructuredGateway
             -> private request JSON
             -> ollama_worker.py in Hermes Python environment
             -> load effective Hermes config
             -> resolve providers.ollama-local
             -> native POST /api/chat with JSON Schema
             -> extract message.content
             -> ModelInvocationResult
             -> existing validate_digest()
             -> existing render / dry-run or delivery / checkpoint
```

The two paths converge only at the existing `ModelInvocationResult` / validation boundary.

## 5. Proposed code structure

New files:

```text
whatsapp_digest/model/
  router.py
  ollama.py
  ollama_worker.py
```

Existing files changed minimally:

```text
whatsapp_digest/runner.py
whatsapp_digest/config.py
README.md
tests/
pyproject.toml
```

`hermes.py` and `hermes_worker.py` should remain behaviorally unchanged unless a tiny reusable helper can be shared with zero semantic change. Avoid refactoring the stable Hermes path merely to make the new code look symmetrical.

## 6. Gateway routing

`router.py` owns the explicit selection rule.
Conceptually:

```python
OLLAMA_STRUCTURED_PROVIDER = "ollama-local"

def gateway_for_workflow(config, workflow):
    if workflow.model["provider"] == OLLAMA_STRUCTURED_PROVIDER:
        return OllamaStructuredGateway(config)
    return HermesModelGateway(config)
```

The runner keeps test dependency injection:

```python
model_gateway = gateway or gateway_for_workflow(config, workflow)
```

No message content is examined by the router.

The provider key is trusted configuration, not message classification.

## 7. Ollama configuration contract

v2.2 intentionally supports the existing named provider:

```yaml
providers:
  ollama-local:
    api: http://127.0.0.1:11434/v1
    transport: chat_completions
    models:
      - qwen3.5:9b
      - qwen3.5:4b
```

This remains in Hermes configuration. The digest does not introduce a second Ollama endpoint setting.

The Ollama worker runs in the Hermes Python environment and uses Hermes' own effective config loader to obtain the provider definition. This keeps Hermes configuration as the single source of truth.
The native Ollama URL is derived deterministically from the configured provider URL.

For the current provider:

```text
configured: http://127.0.0.1:11434/v1
native:     http://127.0.0.1:11434/api/chat
```

Implementation requirements:

- parse the URL with `urllib.parse`, not string replacement;
- require `http` or `https`;
- require a host;
- reject query strings and fragments;
- derive the origin (`scheme://netloc`) and append `/api/chat`;
- never accept an endpoint from WhatsApp/model content.

For v2.2, `provider: ollama-local` requires:

- a concrete model name; `name: inherit` is rejected;
- `reasoning: none`; other reasoning values are rejected for this path.

This avoids silently ignoring trusted configuration while `think: false` is fixed by the structured-output contract.

## 8. Prompt and historical-context behavior

The Ollama path reuses the same v2.1 semantic inputs:

- `BASE_SYSTEM_PROMPT`;
- workflow-specific trusted instructions;
- configured sections;
- historical-context block;
- current-message block;
- current prompt-size policy;
- oldest-context-first trimming when needed.

Do not create a second Ollama-specific summarization prompt in v2.2.
The transport may add only a short final reminder such as "return the response according to the supplied schema" if testing shows it materially improves compliance. It must not duplicate business logic already encoded in the common prompt.

The native Ollama path must return the same `source_records` and actually-used `context_source_records` semantics as `HermesModelGateway`.

## 9. JSON Schema

The schema is generated from trusted workflow configuration for every request.

Top-level shape:

```json
{
  "type": "object",
  "properties": {
    "sections": {
      "type": "array",
      "items": { "... section schema ..." }
    }
  },
  "required": ["sections"],
  "additionalProperties": false
}
```

Each item requires exactly the current digest fields:

- `title`: string
- `summary`: string
- `details`: array of strings
- `actions`: array of strings
- `questions`: array of strings
- `reported_by`: array of strings
- `contributors`: array of strings
- `references`: array of strings
- `source_ids`: non-empty array of strings
All object levels use `additionalProperties: false`.

If the workflow has configured sections, `section_id` is constrained with an `enum` containing exactly those configured IDs.

If the workflow has no configured sections, `section_id` remains a bounded string and `title` remains a string, preserving current dynamic-section behavior.

The schema may mirror existing safe count limits such as maximum sections/items, but semantic/source validation remains in `validate_digest()`.

### 9.1 Deliberately not encoded in JSON Schema

Do not enumerate current message IDs as a `source_ids` enum.

Do not enumerate participant names.

Do not attempt to encode "at least one current source" into the transport schema.

Reasons:

- avoids rebuilding semantic authorization in two places;
- avoids large per-run schemas;
- keeps historical/current semantics centralized;
- preserves `validate_digest()` as the source-of-truth validator.

## 10. Native Ollama request

The intended request is:

```json
{
  "model": "qwen3.5:9b",
  "stream": false,
  "think": false,
  "messages": [
    {"role": "system", "content": "<existing BASE_SYSTEM_PROMPT>"},
    {"role": "user", "content": "<existing built user prompt>"}
  ],
  "format": {"...": "generated JSON Schema"},
  "options": {
    "temperature": 0
  }
}
```

These values are fixed in v2.2:

- `stream: false`
- `think: false`
- `temperature: 0`

They are not new user-tuning settings.

The existing `hermes.timeout_seconds` remains the single model-generation timeout. No Ollama-specific timeout is added.

## 11. HTTP implementation

Use Python standard-library HTTP from `ollama_worker.py`; do not add an Ollama SDK dependency merely for one POST.

Recommended primitives:

- `urllib.request.Request`
- `urllib.request.urlopen`
- `json.dumps/json.loads`

The parent gateway continues to enforce the overall subprocess timeout.

The worker must:

1. load the effective Hermes configuration;
2. find the exact `providers.ollama-local` mapping;
3. verify the requested concrete model is permitted when a provider model allowlist exists;
4. derive the native endpoint;
5. send the request;
6. reject non-success HTTP responses with a concise error;
7. parse the Ollama response envelope;
8. require a non-empty string at `message.content`;
9. return only that content plus provider/model/reasoning metadata.

Normal logs must never include prompts, source messages or full model output.
## 12. Result and validation boundary

`OllamaStructuredGateway.summarize()` returns the existing `ModelInvocationResult`:

```text
raw_output
provider = ollama-local
model = concrete model name
reasoning = none
source_records
context_source_records
```

The runner then executes the exact same existing operations:

```text
validate_digest()
render_digest()
render_raw_messages()
freshness check
delivery
checkpoint
retention
```

If structured generation still returns content that cannot pass `validate_digest()`, it is a validation failure, not something the transport repairs.

The existing opt-in raw-output debug capture remains the troubleshooting mechanism for completed model responses that fail validation.

## 13. Failure semantics

### Model-stage failures

These are reported through the existing model-failure path and never advance the checkpoint:

- missing `ollama-local` provider config;
- missing/invalid provider URL;
- `name: inherit` on the Ollama structured path;
- unsupported reasoning setting;
- model not allowed by configured provider list;
- connection refused;
- HTTP error;
- timeout;
- invalid Ollama response envelope;
- missing/empty `message.content`.
### Validation-stage failures

These remain validation failures:

- malformed generated JSON content;
- structurally unexpected data that escaped provider enforcement;
- unknown source IDs;
- context-only items;
- unknown participants;
- unsupported URLs;
- configured-section violations;
- other existing `validate_digest()` failures.

There is no automatic fallback or retry in either case.

## 14. Privacy and security

The v2/v2.1 boundaries remain unchanged:

- WhatsApp text is untrusted data;
- prompts and source bodies do not appear in process arguments;
- request/result temp directories are owner-only;
- temp JSON files are owner-only;
- SMTP recipients and schedules remain config-only;
- raw model output is persisted only when the existing opt-in debug environment variable is enabled and validation fails;
- model/debug failures never advance checkpoints.

The Ollama endpoint and model name come only from trusted configuration.

## 15. Observability

Add operational metadata only:

```text
provider=ollama-local
model=qwen3.5:9b
reasoning=none
structured_output=true
generation_seconds=<value>
```

Existing run status and failure-stage fields remain authoritative.

Do not log message bodies, prompts, JSON Schema contents or raw generated output during normal operation.
## 16. Implementation plan

### Phase 1 — Structured transport and deterministic integration

Goal: implement the separate Ollama path without changing normal Hermes behavior.

Implementation:

1. Add `model/router.py` with exact `ollama-local` routing.
2. Add config validation for concrete Ollama model + `reasoning: none`.
3. Add `model/ollama.py` implementing `OllamaStructuredGateway`.
4. Add `model/ollama_worker.py` executed with the Hermes Python environment.
5. Reuse existing prompt/source/context construction.
6. Add workflow-driven schema builder.
7. Implement native `/api/chat` POST.
8. Return existing `ModelInvocationResult`.
9. Change the runner only to obtain its default gateway through the router.
10. Keep validation/rendering/delivery/checkpoint code unchanged.
11. Add focused unit/integration tests.
12. Update README with the explicit Ollama 2.2 contract.

Exit criterion: automated tests prove routing, request shape, schema, errors and existing-provider regression behavior.

### Phase 2 — Live validation and quality gate

Goal: prove that schema reliability did not hide weak summarization quality.

Implementation/verification:

1. Run a live dry-run with `qwen3.5:9b`.
2. Confirm native structured generation passes JSON parsing and application validation.
3. Confirm historical context works on a continuation case.
4. Inspect raw source attachment against generated digest.
5. Run the defined representative quality cases.
6. Record findings before declaring v2.2 ready.
7. Update architecture/handoff docs with final implementation state.

No automatic retry/fallback is added during Phase 2. If quality is insufficient, stop and evaluate model/prompt quality separately rather than hiding it with pipeline complexity.

## 17. Automated test plan

### 17.1 Router regression tests

- `ollama-local` selects `OllamaStructuredGateway`.
- `inherit` selects existing `HermesModelGateway`.
- `openai-codex` selects existing `HermesModelGateway`.
- another named custom provider selects existing `HermesModelGateway`.
- provider names that merely contain the text "ollama" do not implicitly route unless exactly configured for this v2.2 path.
- routing result is independent of all message text.

### 17.2 Config tests

- `ollama-local + concrete model + reasoning:none` is valid.
- `ollama-local + name:inherit` is rejected.
- `ollama-local + reasoning:medium/high/inherit` is rejected for v2.2.
- non-Ollama provider configuration retains existing validation behavior.
- v2.1 legacy-config compatibility tests remain green.

### 17.3 Schema tests

- top-level `sections` required.
- configured section IDs become the exact enum.
- dynamic sections remain possible when none are configured.
- every item field is required with the correct type.
- `references` is array-of-strings, preventing the shape observed in the failed Qwen run.
- `source_ids` is a non-empty array-of-strings.
- `additionalProperties: false` is present at structured object levels.
- schema does not contain source-message text.
- schema does not enumerate runtime source IDs or participant names.

### 17.4 Ollama worker tests

Use a local fake HTTP server; CI must not require a real Ollama installation.

Verify:

- endpoint derivation from the configured `/v1` URL;
- request uses `/api/chat`;
- `stream=false`;
- `think=false`;
- `temperature=0`;
- generated schema is supplied through `format`;
- system and user messages are sent separately;
- successful envelope extracts `message.content`;
- HTTP errors become model-stage failures;
- invalid JSON envelope fails clearly;
- empty content fails clearly;
- missing provider config fails clearly;
- model allowlist mismatch fails clearly;
- secrets/source text are not emitted in error messages.

### 17.5 Runner/integration tests

- Ollama result passes through existing `validate_digest()`.
- validation failure does not move the checkpoint.
- model failure does not move the checkpoint.
- dry-run never delivers or moves the checkpoint.
- successful real run follows existing delivery/checkpoint path.
- historical-context records and current records are passed identically to the common prompt builder.
- context-only generated items are still rejected.
- source freshness behavior remains unchanged.
- raw QA export remains unchanged.
- debug capture still works for validation failures.
- timeout remains governed by `hermes.timeout_seconds`.

### 17.6 Existing-flow regression test

The entire existing v2 test suite must remain green.

Add at least one explicit test asserting that the non-Ollama branch invokes `HermesModelGateway` exactly as before. The test should compare behavior, not message keywords.

## 18. Quality benchmark

Structured JSON is necessary but not sufficient. Before release, review at least three representative real or sanitized windows:

1. **Continuation/context case** — a short current reply whose meaning depends on prior processed messages.
2. **Multi-topic technical case** — several unrelated technical discussions in one source window.
3. **Noise/acknowledgement case** — useful technical content mixed with conversational chatter and acknowledgements.

The benchmark is semantic, not lexical. There is no expected keyword list.

For each case review:

- major technical facts captured;
- critical facts omitted;
- unsupported/fabricated claims;
- versions, commands, paths and URLs preserved accurately;
- actions and unresolved questions represented when material;
- historical context used only to understand current material;
- trivial conversation not promoted into standalone technical updates;
- final digest usefulness for the technical team.

GPT output may be used as a comparison reference, but exact GPT parity is not a release requirement.
### Quality acceptance bar

For the benchmark set:

- zero critical unsupported claims;
- zero fabricated commands, versions, paths or URLs;
- no context-only digest items;
- no source-attribution violations;
- all clearly material technical topics represented;
- no repeated systemic promotion of trivial chatter into digest items;
- all outputs pass normal application validation without repair.

Any failure is documented with the source window and model output before deciding whether the problem belongs to model quality, prompt quality or transport/schema behavior.

## 19. Definition of Done

v2.2 is done only when all of the following are true:

1. `provider: ollama-local` uses the dedicated native structured-output path.
2. Every other provider uses the existing Hermes path with no behavior change.
3. No automatic provider/model fallback exists on the Ollama path.
4. No code performs lexical/keyword message classification.
5. Ollama endpoint/model selection comes only from trusted configuration.
6. Ollama uses native `/api/chat`, JSON Schema `format`, `stream:false`, `think:false`, and `temperature:0`.
7. The schema is generated from workflow configuration and prevents the structural failures observed in v2.1 testing.
8. `validate_digest()` remains the semantic/source authority.
9. Historical-context and current-source invariants remain unchanged.
10. One generation call is performed per run; no repair/retry call is introduced.
11. Model, validation, delivery and freshness failures never advance checkpoints.
12. Debug output remains opt-in and private.
13. New automated tests pass.
14. The complete pre-existing test suite passes.
15. `git diff --check` is clean.
16. A real `qwen3.5:9b` dry-run completes through structured output and normal validation.
17. The representative quality benchmark meets the acceptance bar.
18. README documents configuration, expected behavior, errors and troubleshooting.
19. Architecture/handoff documentation reflects the implemented state.
20. The feature is reviewed on its own PR before merge.

## 20. Deferred by design

Do not add these in v2.2 unless evidence from the implementation proves they are necessary:

- automatic validation-feedback retry;
- JSON repair/extraction;
- cloud fallback;
- generic local-provider abstraction;
- arbitrary OpenAI-compatible endpoint support;
- configurable temperature/top-p/top-k;
- thinking/reasoning levels;
- source-ID enums in JSON Schema;
- persistent model-quality scoring;
- lexical classifiers;
- multiple summarization calls.

These can be reconsidered in later versions based on observed failures rather than anticipated complexity.

## 21. External interface references

Ollama structured outputs:
https://ollama.com/blog/structured-outputs

Ollama thinking control:
https://ollama.com/blog/thinking

These references define the external API behavior used by the design. The repository tests and runtime validation remain authoritative for application behavior.
