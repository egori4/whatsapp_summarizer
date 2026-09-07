# Architecture and threat model

## System purpose

The project is a controlled, local-first WhatsApp group digest pipeline. It is designed to collect exactly one approved group without conversational-agent behavior, summarize grounded content locally, and later deliver one fixed-recipient digest. It is not active until all staged gates are satisfied.

## Component architecture

```text
Approved WhatsApp group (future, exact opaque JID)
  -> Baileys bridge on 127.0.0.1:17778
  -> bridge intake mode + exact group allowlist
  -> G2c critical collector hook (fail closed on hook error)
  -> durable SQLite spool (owner-only)
  -> deterministic snapshot / revision / coverage checks
  -> local Ollama on 127.0.0.1:11434
       4B high-recall selection -> 9B final digest -> 4B fallback
  -> fixed-recipient SMTP transport (separate G2d gate)
  -> no-agent scheduled wrapper (separate G3 gate)
```

The bridge is the only WhatsApp-connected component. Ollama is an isolated Docker service with no WhatsApp configuration. The runner is deterministic around model and delivery boundaries: a source revision must still be current and non-revoked before model processing and again before delivery.

## Current deployment state

- **Provisioned:** pinned loopback Ollama runtime, approved local model identities, protected policy (`0600`), preserved WhatsApp session (`0700`), project code and offline tests.
- **Prepared but unapplied:** current-source Hermes fail-closed patch.
- **Provisioned but inactive:** Hermes WhatsApp runtime configuration. It uses bot bridge mode plus a preliminary pairing gate, while the adapter independently denies DMs and allows one exact group only.
- **Not provisioned:** gateway user unit, bridge listener, spool database, SMTP environment/transport, scheduler job.
- **Current blocker:** G2c still needs a fresh preflight and explicit confirmation before the reviewed source patch may be applied and the gateway started.

## Data and state boundaries

| Asset | Location / boundary | Control |
|---|---|---|
| WhatsApp session credentials | Protected local session directory | `0700`; never print/copy/delete without approval |
| Group content / participant IDs | Future bridge to owner-only spool | Exact target JID; no normal agent dispatch |
| SQLite spool / digest metadata / provenance | Project data directory | Directory `0700`, database `0600`, revision-only provenance hash-bound to the rendered digest; retention 7/90 days |
| Ollama prompts and responses | Loopback Docker service | No LAN/public port; pinned local model identities |
| SMTP credential | Future protected environment file | Reference-only policy name; no password in Git/logs/CLI |
| Policy | `config/digest.policy.json` | `0600`, Git-ignored, schema/live-safety validation |

## Security controls

1. **Exact identity:** only one opaque `@g.us` JID may pass the group allowlist. The project accepts the approved numeric-hyphen form but rejects malformed identifiers.
2. **DM denial:** the eventual adapter policy disables all DMs. A bridge-level temporary `pairing` mode is needed only because the current bridge gates group participants too early; the adapter still blocks DMs before dispatch.
3. **Independent outbound guard:** collector-only mode denies all bridge POST output, including future unknown POST routes, before socket/network use.
4. **Fail-closed collector:** a target-event record-conversion, spool-initialization, or spool-write failure returns a generic local `skip` directive before normal Hermes dispatch. Failure telemetry contains only the exception type, never event content, participant, message ID, or JID.
5. **No receipt / presence side effects:** read receipts are disabled; typing, progress, sends, edits, media, polls, and location operations are denied.
6. **Source integrity:** immutable revisions, cutoff snapshots, omission tracking, and revocation rechecks prevent stale/revoked source text from entering a delivered digest.
7. **Local-only inference:** external fallback is disabled. Model tags are not trusted alone; policy requires immutable SHA-256 identities.
8. **Delivery safety:** fixed recipient, no recipient CLI override, explicit SMTP acceptance/unknown reconciliation, and no checkpoint advance on uncertainty.
9. **Schedule safety:** no cron activation unless both DST probes produce 08:00 America/Toronto.
10. **Audit provenance:** an accepted or delivery-unknown validated one-pass digest records only ordered immutable `(message_id, change_seq)` mappings, a schema version, and a canonical hash. It rejects raw prose fields, stale/revoked revisions, and failed candidates.
11. **Execution separation:** `validate-only` is the default and cannot build a model, deliver, record a digest run, or advance a checkpoint; `render-only` can render only; only explicit `delivery-capable` validates live policy and can reach SMTP/checkpoint state.
12. **Hermes size bounds:** before Hermes runs, the complete UTF-8 prompt must fit `context_limit`; returned stdout must fit `max_output_chars` before parsing. Both overflow paths fail closed without truncation or a prompt file/model invocation for input overflow.
13. **Collector SQLite lifecycle:** registration and each target hook close their own thread-affine connection; appends wait at most 250 ms per SQLite lock attempt and retry once only. Exhaustion reaches the existing generic target-event skip boundary.
14. **Actionable isolation:** one-pass schema construction, validation/canonicalization, provenance mapping, prompting, and reader rendering live in dedicated modules. `models.py` compatibility entry points preserve the legacy two-stage pipeline contract; focused parity tests guard the boundary.

## Threats and residual risk

| Threat | Mitigation | Residual risk / stop condition |
|---|---|---|
| Wrong group or unintended group expansion | Exact sole-JID allowlist and protected policy | Stop on wildcard, alias, blank, or second JID |
| Collector failure causes agent output | G2c fail-closed hook + bridge outbound guard | Installed source remains unsafe until G2c is verified |
| Bridge admits DMs while collecting groups | Bot bridge mode plus adapter DM policy disabled | Stop if adapter policy is not independently verified |
| Read/typing/output side effect | POST guard before route dispatch | Stop on any non-403 target output route |
| Data loss before spool write | Coverage/health intervals, durable omission block | Cannot reconstruct pre-collector loss |
| Model hallucination / failure | Source reference validation and local fallback | Both local model failures produce no deliverable |
| Local model exposure | `127.0.0.1:11434` binding only | Stop on LAN/public listener or digest mismatch |
| SMTP exfiltration or duplicate email | Fixed recipient / Message-ID reconciliation | Transport is blocked pending G2d approval |
| Incorrect run time at DST | Spring/fall wall-clock probes | Current scheduler fails; cron stays disabled |

## Non-goals

- No WhatsApp pairing or re-pairing during normal deployment.
- No direct group reply, command handling, or chatbot session for the selected group.
- No external model, web retrieval, or external fallback.
- No SMTP delivery or scheduler activation without separately recorded approval.
