# WhatsApp Technical Digest v2.2 — Ollama Structured Output Architecture

**Status:** Implemented on feature branch; automated and live validation complete
**Branch:** `v2.2-ollama-structured-output`
**Baseline:** merged v2.1 on `main` (`549e989`)
**Package version on feature branch:** `2.2.0`
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
- automated tests and a small manual real-message quality review.

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

This path is not refactored for v2.2.

### 4.2 New v2.2 Ollama path

```text
runner
  |
  +-- workflow.model.provider == "ollama-local"
  |     -> OllamaStructuredGateway
  |     -> read providers.ollama-local from the Hermes config file
  |     -> native POST /api/chat with JSON Schema
  |     -> extract message.content
  |     -> ModelInvocationResult
  |
  +-- every other provider
        -> existing HermesModelGateway unchanged

ModelInvocationResult
  -> existing validate_digest()
  -> existing render / dry-run or delivery / checkpoint
```

The new code ends at the existing `ModelInvocationResult` boundary. Everything after that boundary is shared.

## 5. Proposed code structure

Add one production module:

```text
whatsapp_digest/model/
  ollama.py
```

Change only what is required around it:

```text
whatsapp_digest/runner.py   # small explicit gateway selection
README.md
tests/
pyproject.toml              # version bump only
```

Do not add a provider router framework or a second worker process for Ollama.

`hermes.py` and `hermes_worker.py` remain behaviorally unchanged. Do not refactor the stable Hermes path merely to make the two implementations symmetrical.

## 6. Gateway selection

The existing runner performs one explicit trusted-configuration check:

```python
if gateway is not None:
    model_gateway = gateway
elif workflow.model["provider"] == "ollama-local":
    model_gateway = OllamaStructuredGateway(config)
else:
    model_gateway = HermesModelGateway(config)
```

That is intentionally the whole routing design for v2.2.

There is no registry, capability system, adapter discovery, provider-name pattern matching, or transport abstraction.

No message content is examined when selecting a gateway. The provider key is trusted workflow configuration and is unrelated to message classification.

## 7. Ollama configuration contract

v2.2 uses the existing named provider in Hermes configuration as the single endpoint source:

```yaml
providers:
  ollama-local:
    api: http://127.0.0.1:11434/v1
    transport: chat_completions
    models:
      - qwen3.5:9b
      - qwen3.5:4b
```

The digest does not introduce a second Ollama endpoint setting.

`OllamaStructuredGateway` reads the Hermes config file directly with the project's existing YAML dependency and retrieves only `providers.ollama-local.api`. The path follows the existing `HERMES_HOME` convention when set, otherwise `~/.hermes/config.yaml`. It does not start Hermes, import Hermes runtime code, or execute a second worker process.

The native Ollama URL is derived deterministically from the configured provider URL:

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

For the native Ollama path:

- `name` must be a concrete model; `name: inherit` is rejected;
- `reasoning: none` is accepted;
- `reasoning: inherit` is accepted and resolves to `none` for this path;
- explicit `minimal/low/medium/high/xhigh` values are rejected because v2.2 always sends `think: false`.

Do not validate the model against the optional Hermes provider `models:` list. Ollama itself is authoritative for whether the requested model exists and can be loaded.

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

Keep the schema focused on structural shape. Do not duplicate the application's detailed length/count, participant, source-authorization, URL, or current-vs-context policies in JSON Schema; those remain centralized in `validate_digest()`. The one intentional structural constraint beyond types is that `source_ids` must contain at least one string.

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

Use Python standard-library HTTP directly from `ollama.py`; do not add an Ollama SDK dependency or a subprocess merely for one POST.

Recommended primitives:

- `urllib.request.Request`
- `urllib.request.urlopen`
- `urllib.parse.urlsplit/urlunsplit`
- `json.dumps/json.loads`

`OllamaStructuredGateway` uses the existing `hermes.timeout_seconds` value as the HTTP request timeout.

The gateway must:

1. read the Hermes config file from `HERMES_HOME/config.yaml` or the default `~/.hermes/config.yaml`;
2. find the exact `providers.ollama-local` mapping and its `api` value;
3. derive the native endpoint;
4. build the existing semantic prompt and structural schema;
5. send the request;
6. reject HTTP/network errors with a concise model-stage error;
7. parse the Ollama response envelope;
8. require a non-empty string at `message.content`;
9. return that content in the existing `ModelInvocationResult`.

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

### Phase 1 — Implement the narrow Ollama path

Goal: add one direct structured-output gateway without changing normal Hermes behavior.

Implementation:

1. Add `model/ollama.py` implementing `OllamaStructuredGateway`.
2. Reuse the existing source-record, prompt and historical-context builders.
3. Read only `providers.ollama-local.api` from the Hermes config file (`HERMES_HOME/config.yaml` when set, otherwise `~/.hermes/config.yaml`).
4. Add a small workflow-driven structural JSON Schema builder inside `ollama.py` or as a nearby private helper.
5. Implement the native `/api/chat` request with standard-library HTTP.
6. Return the existing `ModelInvocationResult`.
7. Add the small explicit `ollama-local` gateway selection in `runner.py`, preserving injected test gateways.
8. Keep `HermesModelGateway`, `hermes_worker.py`, validation, rendering, delivery and checkpoint code behaviorally unchanged.
9. Add focused tests for gateway selection, request shape, schema and error handling.
10. Update README with the explicit Ollama 2.2 behavior.

Exit criterion: automated tests prove the new path works and all existing-provider behavior remains unchanged.

### Phase 2 — Prove it with real output

Goal: confirm that structural reliability produces a useful digest rather than merely valid JSON.

Verification:

1. Run a live dry-run with `qwen3.5:9b` on a representative source window.
2. Confirm native structured generation passes normal JSON parsing and `validate_digest()`.
3. Run a continuation case that uses historical context.
4. Inspect the raw source attachment against the generated digest.
5. Review two or three representative source windows using the manual semantic checklist in Section 18.
6. Record any misses or unsupported claims before declaring v2.2 ready.
7. Update architecture/handoff docs with the final implementation state.

Do not build benchmark infrastructure for Phase 2. This is a manual engineering quality gate. No automatic retry/fallback is added if the model performs poorly.

## 17. Automated test plan

### 17.1 Gateway-selection regression tests

- exact `ollama-local` selects `OllamaStructuredGateway`;
- `inherit` selects the existing `HermesModelGateway`;
- `openai-codex` selects the existing `HermesModelGateway`;
- another named provider selects the existing `HermesModelGateway`;
- gateway selection is independent of all message text;
- an explicitly injected test gateway still overrides default selection.

### 17.2 Ollama configuration behavior

- `ollama-local + concrete model + reasoning:none` works;
- `ollama-local + concrete model + reasoning:inherit` resolves to `none`;
- `ollama-local + name:inherit` fails clearly before an HTTP call;
- explicit thinking levels such as `low/medium/high/xhigh` fail clearly for this path;
- missing or malformed `providers.ollama-local.api` fails clearly;
- non-Ollama workflow configuration retains existing behavior;
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

### 17.4 Ollama gateway tests

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
- HTTP/network errors become model-stage failures;
- invalid JSON envelope fails clearly;
- empty content fails clearly;
- missing provider config fails clearly;
- a model name is passed through to Ollama without maintaining a duplicate allowlist;
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

## 18. Manual quality gate

Structured JSON is necessary but not sufficient. Do not build benchmark infrastructure for v2.2. Before release, manually review at least three representative real or sanitized windows:

1. **Continuation/context case** — a short current reply whose meaning depends on prior processed messages.
2. **Multi-topic technical case** — several unrelated technical discussions in one source window.
3. **Noise/acknowledgement case** — useful technical content mixed with conversational chatter and acknowledgements.

The review is semantic, not lexical. There is no expected keyword list, golden-output framework, scoring database, or automated model judge.

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

Across the manually reviewed cases:

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
17. The representative manual quality review meets the acceptance bar.
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

## 22. Implementation validation results

Implementation completed on `v2.2-ollama-structured-output` with the simplified design:

- one new production module: `whatsapp_digest/model/ollama.py`;
- one explicit `ollama-local` gateway selection in `runner.py`;
- no router framework;
- no Ollama worker subprocess;
- no Hermes/OpenAI-path refactor;
- native `/api/chat` with JSON Schema structured output;
- existing semantic validation, rendering, freshness, delivery and checkpoint logic unchanged.

### Automated validation

Final local suite:

```text
138 passed
git diff --check: clean
```

Coverage includes native endpoint derivation, request shape, structured schema, `stream:false`, `think:false`, `temperature:0`, HTTP/timeout/envelope errors, explicit model/reasoning rules, exact gateway selection, non-Ollama regression behavior, model-failure checkpoint safety and existing v2/v2.1 tests.

### Live qwen3.5:9b validation

Case 1 — normal checkpoint-driven dry-run:

- 1 current message + 37 historical-context messages;
- current message was only a thank-you acknowledgement;
- generation completed in about 149 seconds;
- structured output passed `validate_digest()`;
- result correctly contained no material updates;
- dry-run did not advance the checkpoint or send email.

Case 2 — focused VM profile technical discussion:

- 8 current messages, no historical context;
- generation completed in about 480 seconds;
- structured output passed `validate_digest()`;
- preserved the profile path, custom-profile procedure, reboot requirement and supportability concern;
- correctly ignored the trailing thank-you;
- quality caveat: duplicated the same topic into Technical Updates and Actions / Follow-up and kept an already answered question in the questions list.

Case 3 — six-message multi-topic direct gateway/validator review:

- GEL/CLS licensing;
- software-version PS BP question;
- Cyber Controller RAM question;
- AppWall + SecurePath support question;
- generation completed in about 881 seconds;
- output passed `validate_digest()`;
- all material topics were represented with valid source attribution;
- no fabricated commands, versions, paths or URLs were observed;
- quality caveat: open questions were duplicated between Technical Updates and Unanswered Technical Questions, and the GEL question was slightly reframed.

### Quality-gate conclusion

The documented release acceptance bar is met: no critical unsupported technical claim was identified, no fabricated commands/versions/paths/URLs were observed, source validation passed, context-only material was not emitted, material topics were represented, and trivial acknowledgement text was not promoted.

The local 9B model is nevertheless editorially weaker than the cloud model. The observed duplication and answered-question handling should be treated as model-quality limitations, not transport/schema defects. v2.2 intentionally does not add repair calls, lexical classifiers, fallback, or Ollama-specific semantic heuristics to mask those weaknesses.

### Remaining DoD item

All implementation/test/documentation DoD items are satisfied except the final repository process item: the feature must still be reviewed on its own PR before merge.

### 2026-09-30 truncation fix

A live `--last 1d` run with 33 messages exposed an Ollama runtime-budget issue that was not visible in the initial smaller acceptance cases.

Observed failure:

- Ollama returned only 1,288 bytes of model content;
- output ended in the middle of a `source_id` string;
- application validation correctly rejected it as unterminated JSON;
- Ollama server logs showed Qwen running with a 4,096-token context despite the model supporting 262,144 tokens;
- the same run's prompt was already about 6,658 user-prompt characters plus 2,845 system-prompt characters before generation.

Root cause: the native v2.2 request did not explicitly set Ollama context/output budgets, so the local runtime's 4k context could exhaust the available generation space and truncate otherwise schema-constrained JSON.

Fix:

- set `num_ctx: 32768` for the native Ollama digest request;
- set `num_predict: 4096` to reserve a bounded output budget;
- inspect Ollama `done_reason`;
- treat `done_reason: length` as a clear model-stage truncation error with `prompt_eval_count`, `eval_count`, `num_ctx`, and `num_predict` in the diagnostic;
- log successful `done_reason`, prompt-token count, and output-token count without source content.

Regression coverage verifies both request options and explicit length-truncation handling.

The exact failed command was rerun after the fix:

```bash
WHATSAPP_DIGEST_DEBUG_MODEL_OUTPUT=1 digest --config ./config.yaml run global-ps --last 1d --dry-run
```

Result:

- 33 messages processed;
- Ollama was verified at process level with `-c 32768`;
- generation completed;
- structured JSON passed the unchanged application validator;
- dry-run completed successfully with no checkpoint advance.
