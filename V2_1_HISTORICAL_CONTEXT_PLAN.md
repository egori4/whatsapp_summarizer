# WhatsApp Technical Digest v2.1 — Historical Context Lookback

**Status:** Implemented on feature branch pending review and live validation  
**Baseline:** v2.0.0 (`8082c9a`)

## Goal

Allow a digest to understand conversations that continue across successful digest checkpoints without re-summarizing old material. Keep the v2 architecture small: no persistent AI memory, embeddings, topic database, second model call, or Hermes changes.

## Configuration

Historical context is global and enabled by default:

```yaml
context:
  enabled: true
  lookback: 48h
  max_messages: 100
```

`lookback` accepts the existing duration style (`24h`, `48h`, `3d`). `enabled: false` restores v2.0 behavior. The enabled lookback must not exceed `retention.processed_raw_days`.

## Selection semantics

For a normal initialized run:

- **Current:** `checkpoint_seq < change_seq <= cutoff_seq`.
- **Context:** same group, already processed (`change_seq <= checkpoint_seq`), within the configured lookback, usable and not deleted.
- Context is capped to the newest `max_messages`, then presented chronologically.
- Explicit `--last` and `--since` runs do not add checkpoint context.
- If there are no usable current messages, context alone never causes a model call.

## Model boundary

The existing one-shot Hermes invocation receives two clearly labelled untrusted blocks: `HISTORICAL CONTEXT` and `CURRENT MESSAGES`. Historical records may resolve references, short answers, corrections and prior state, but may not create a digest item by themselves.

The prompt keeps current messages at higher priority. If the 160k prompt limit is exceeded, oldest context records are dropped first; current messages are never dropped merely to preserve history.

## Deterministic validation

All cited source IDs must belong to the exact model-visible source set. In addition, every digest item must cite at least one current source ID. Historical IDs may be cited alongside current IDs when needed for grounding. This prevents old topics from being emitted again solely because they were supplied as context.

## Checkpoint and delivery invariants

Historical context does not alter message count, digest current window, cutoff, initialization, or checkpoint movement. Freshness checks cover both current records and context records actually supplied to the model. Delivery and retry semantics remain unchanged.

## QA and observability

Digest metadata reports the number of prior context messages and lookback. The raw QA attachment keeps the exact model-visible records and separates them into historical and current sections. Logs record only counts/lookback, never WhatsApp bodies.

## Retention

No new storage is introduced. Context reads the existing retained SQLite messages. The configured raw-message retention remains the privacy/storage authority.

## Files changed

- `config.example.yaml`, `whatsapp_digest/config.py`
- `whatsapp_digest/database.py`
- `whatsapp_digest/model/hermes.py`
- `whatsapp_digest/validation.py`
- `whatsapp_digest/runner.py`
- `whatsapp_digest/renderer.py`, `whatsapp_digest/raw_export.py`
- v2 unit tests and README/version metadata

## Definition of Done

1. Full v2 unit suite passes.
2. Configuration defaults to enabled / 48h / 100 messages and can be disabled.
3. Context-only model items are rejected deterministically.
4. Explicit manual windows retain their existing semantics.
5. Full live checkpoint-driven dry-run on m920q sees historical context without moving the checkpoint or sending email.
6. Raw QA artifact separates current and historical records.
7. Existing transport, scheduler, SMTP, retention and security tests remain green.
8. Branch is pushed for review before merge/tagging v2.1.0.
