# v2.5.0 — Weekly Digest Implementation Plan, Tests and DoD

**Status:** Phases A/B implemented on feature branch; guarded production rollout pending
**Target:** v2.5.0 on `feature/v2.5.0-weekly-digest` from clean v2.4.0 `main`
**Architecture contract:** [V2_5_WEEKLY_DIGEST_ARCHITECTURE.md](V2_5_WEEKLY_DIGEST_ARCHITECTURE.md)

## 0. Implementation brief for the next engineer/agent

Build a **weekly roll-up of already validated daily digests** for each opted-in workflow. Do not create a duplicate group workflow or a second collector. Keep existing daily behavior, original checkpoint ordering, BCC delivery, one-shot/no-tools model isolation, and 7-day processed raw retention intact. Add opt-in daily JSON persistence, weekly period selection with an explicit completeness gate, one weekly model call, source-backed validation, HTML/email delivery, a delivery ledger and a separate Monday timer. **No production SMTP sends during development/tests**; use fake model and fake delivery fixtures, then a controlled operator-approved smoke test.

Repository paths below refer to `/home/egori4/scripts/whatsapp-tech-digest` on `m920q`. Development should occur on a feature branch/worktree, never by editing live production code without intentional deployment.

### Required design constraints

1. The current `config.py` rejects duplicate `group_jid`; put `weekly_summary` **inside the existing workflow**.
2. The current `runs` table contains metadata but no digest JSON; store validated daily JSON separately in SQLite.
3. Existing raw-message purge is based on the daily workflow checkpoint; **weekly must never be another raw-message consumer/checkpoint**.
4. The existing daily validator expects WhatsApp message IDs and WhatsApp participant rows. Weekly evidence is **daily digest items**, so it needs a dedicated provenance-aware validation path, while reusing structural validation helpers.
5. Daily model calls currently have distinct Hermes and native Ollama paths. Reuse/refactor their invocation plumbing for a weekly system/user prompt; do not silently fall back from Ollama to a cloud model.
6. Create no new services other than the optional weekly oneshot/timer. Reuse current `workflow_lock` to serialize per-parent-workflow operations.
7. The weekly period is previous Monday 08:30 to current Monday 08:30 **local time**; select successful daily artifacts by completion time with a predecessor anchor and current Monday daily completion check. The period label must not imply exact midnight-to-midnight WhatsApp history.

## 1. Configuration and operator-visible contract

### Proposed YAML (additive)

```yaml
retention:
  processed_raw_days: 7
  log_days: 30
  digest_days: 30

workflows:
  - id: global-ps
    # Existing id/group_jid/schedule/model/summarization/delivery unchanged.
    weekly_summary:
      enabled: true
      schedule:
        type: weekly
        days: [mon]
        at: "08:30"
```

- `weekly_summary` absent => disabled. `enabled: false` => no capture, no weekly timer, no weekly report.
- If enabled, `schedule` defaults to Monday 08:30 (or require explicit schedule; choose one behavior and test it—preferred: explicit schedule as in example). Restrict to one Monday, rather than pretending arbitrary weekly cadence is fully supported by the report-selection logic.
- `retention.digest_days` default 30; enforce a minimum of 14 when any weekly roll-up is enabled, so a full first cycle and retry grace fit. Document clearly. Do **not** alter existing raw-message retention.
- Weekly inherits parent timezone, model/provider/reasoning and email To/Cc/Bcc. It has its own default subject; raw attachment is always suppressed.
- `digest config validate` must reject invalid day/clock/timezone, negative retention and mismatched weekly settings. Existing config upgrade must not rewrite an old user's config unexpectedly.

### Intended CLI

```bash
digest --config ./config.yaml weekly global-ps --dry-run
digest --config ./config.yaml weekly global-ps --period 2026-11-02 --dry-run
digest --config ./config.yaml weekly global-ps
digest --config ./config.yaml schedule install
digest --config ./config.yaml schedule status
digest --config ./config.yaml status global-ps
```

`--period` is the previous Monday **local date**; first example auto-selects the most recent fully elapsed Monday 08:30 weekly cutoff. The command must not generate a future/partially elapsed reporting window. Dry run may record an ordinary `runs` metadata row (consistent with existing daily dry runs), but it must not email, persist a delivered weekly ledger entry, alter daily checkpoints or purge production artifacts.

**Expected weekly unit names:** `whatsapp-digest-global-ps-weekly.service` and `.timer`; leave `whatsapp-digest-global-ps.service/.timer` unchanged.

## 2. Minimal code map

| File | Expected work |
|---|---|
| `whatsapp_digest/config.py` | Optional `weekly_summary` config and `retention.digest_days` validation; no second workflow/JID. |
| `whatsapp_digest/database.py` | `daily_digest_artifacts`, `weekly_reports`; atomic successful daily finalization; period lookups; 30d content purge; weekly idempotency lookup. |
| `whatsapp_digest/runner.py` | Persist a canonical daily artifact at every successful real checkpoint commit; no change to selection/SMTP semantics. |
| `whatsapp_digest/weekly.py` **(new)** | Week key, local-time boundaries, source selection/coverage, weekly AI/render/delivery, run recording and ledger. |
| `whatsapp_digest/model/hermes.py` | Factor/reuse existing safe Hermes call for custom weekly prompt; no duplicated worker bootstrapping. |
| `whatsapp_digest/model/ollama.py` | Reuse existing native structured-output call and preflight for weekly schema, when the parent selects Ollama. |
| `whatsapp_digest/validation.py` | Extract reusable structural checks; add a weekly validator or keep weekly checks adjacent in `weekly.py` if modest. |
| `whatsapp_digest/renderer.py` | Small optional weekly heading/period metadata; reuse HTML and text section rendering. |
| `whatsapp_digest/cli.py` | `weekly` subcommand; dry-run artifacts/status. |
| `whatsapp_digest/systemd.py` | Generate/reconcile optional weekly units without disturbing daily timers. |
| `README.md`, `config.example.yaml`, `HANDOFF.md` | Operators' instructions, examples, limitations and rollback. |
| `tests/test_v2_weekly_*.py` **(new)** | Fast isolated tests; no external SMTP/WhatsApp/model calls. |

Avoid modifying transport, collector, bridge pairing or the public daily JSON schema.

## 3. Phase A — Safe artifact capture (no weekly email yet)

### Build steps

A1. Add `daily_digest_artifacts` storage with `run_id` unique, `workflow_id`, successful `outcome`, ISO UTC completion time, `checkpoint_before/after`, message time range, validated JSON and schema version. Add period index. Use existing `DigestDatabase._initialize()` startup migration convention (`CREATE TABLE IF NOT EXISTS`).

A2. Add `weekly_summary` opt-in and `digest_days` retention config. Existing untouched configs must parse identically. Capture artifacts only for opted-in parent workflows.

A3. Refactor success finalization in `runner.py` / `database.py` to ensure **artifact insertion, `runs` success update, and workflow checkpoint update are one SQL transaction**. Persist `{"sections": []}` for success-no-pending/no-usable/no-material cases. Keep the existing order: model/validate/render -> send (if material) -> transactional success/artifact/checkpoint -> raw/artifact retention.

A4. Keep dry runs, explicit replay windows (`--last`, `--since`), failures and unsuccessful delivery **out** of the artifact table. They may continue to be recorded in `runs` as before. Explicit replay should never alter previously captured canonical artifacts.

A5. Add simple artifact retention cleanup: old daily JSON 30d; do not remove current/recent sources needed for a weekly report. Ensure cleanup does not change the existing `messages` purge logic.

### Phase A automated tests

- `test_existing_config_without_weekly_is_unchanged`: load v2.4 fixtures; same effective daily semantics.
- `test_config_weekly_opt_in_and_validation`: valid Monday config accepted; invalid weekday/day array/time/timezone/retention rejected.
- `test_success_delivered_persists_validated_json`: exact normalized JSON and checkpoint range stored after fake SMTP acceptance.
- `test_no_pending_no_usable_no_material_are_empty_artifacts`: each success has `sections=[]` and advances checkpoint once.
- `test_dry_run_and_explicit_replay_never_persist_artifact`: zero canonical records, no delivery.
- `test_model_validation_freshness_and_smtp_failure_no_artifact`: no new artifact, checkpoint unchanged.
- `test_atomic_finalize_rolls_back_artifact_and_checkpoint`: inject SQL failure and assert both roll back.
- `test_idempotent_daily_finalize_run_id`: repeated finalize does not duplicate artifacts or move checkpoint backwards.
- `test_old_schema_migrates_in_place`: temp copy of v2.4 schema works; new tables created; historical rows preserved.
- `test_digest_retention_is_separate_from_raw_retention`: digest retention does not extend raw 7d or delete pending messages.

### Phase A DoD

- [x] All prior `tests/test_v2_*.py` pass unchanged.
- [x] Every successful **real canonical** opted-in daily cycle, including empty cycles, stores an artifact atomically with checkpoint advancement.
- [x] Failed, dry-run and explicit-window runs cannot pollute the weekly source table.
- [x] Existing daily email/BCC recipients, content and schedule are unchanged.
- [x] Retention is bounded and an existing v2.4 database upgrades without data loss.
- [x] No new weekly SMTP email or service is active merely because artifact capture code exists.

## 4. Phase B — Weekly generation and delivery

### Build steps

B1. Implement weekly period calculation in `weekly.py`. Use `ZoneInfo` local Monday 08:30 boundaries, convert to UTC and derive stable `(workflow_id, previous Monday date)` period identity. For manual `--period`, require valid Monday and a completed period; protect against future windows and disabled workflows.

B2. Query the **anchor** artifact at or before the start boundary and all canonical daily artifacts in `(start, end]`. Verify: anchor exists; checkpoint lineage contiguous; final artifact corresponds to the current Monday morning daily run and was finalized by the cutoff. Allow catch-up runs to cover missed intervening days; never demand exactly seven artifacts. Treat zero-content but successful runs as coverage. If incomplete, log/return a precise failure stage; do not invoke AI/send email.

B3. Convert each saved daily digest item into a deterministic evidence card (`daily:<run_id>:<section_id>:<index>`). Preserve source daily date, title, technical summary/details, questions/actions, contributor names, references and original message ID provenance. No source raw text required. Validate artifact JSON before creating cards.

B4. Add a **weekly-specific** prompt that instructs the model to synthesize a review (not concatenate daily items) in exactly three section IDs: `key_learnings`, `issues_resolutions`, `open_questions_followups`. Merge same issue across days and retain the latest **verified** state; distinguish suggestions/lab tests/production results; preserve unresolved subquestions; do not invent actions, owners, links, versions or outcomes. Cite daily card IDs, one or more per result item.

B5. Reuse current one-shot Hermes invocation and native Ollama structured-response mechanism without duplicating transport/secret handling. Factor common model-call functionality only as far as necessary for a custom prompt/schema; preserve all existing daily prompt and `summarize()` tests. Enforce the same model limits; if weekly input exceeds the cap, fail visibly, not silently truncate. Do not change model/provider when an expected provider is unavailable.

B6. Add weekly-specific structured validation. Reuse appropriate field and length checks from `validation.py`, but change evidence checks to allowed daily **card IDs**. Require cited-card grounding; validate contacts against cited cards, URLs against cited cards, section IDs and max sizes. Model claims still need a human quality review.

B7. Render weekly using existing `render_digest()` with a small, explicit weekly title and period/coverage metadata enhancement. Send via existing `EmailDelivery.send_digest(..., raw_text=None, ...)` to the parent workflow's configured To/Cc/Bcc; use a distinct weekly subject and existing failure-notification policy.

B8. Add `weekly_reports` delivery ledger with unique `(workflow_id, week_start_local_date)`. Hold the existing per-workflow lock across the weekly run. Before AI/SMTP check whether the report was delivered; after SMTP acceptance commit the delivered entry (and optional weekly JSON). Existing daily `workflow_state` is never touched. Empty-but-complete week sends a short "No material updates" report.

B9. Add `digest weekly` CLI and generated weekly systemd oneshot/timer. Extend desired timer reconciliation, `schedule install`, `schedule status` and `digest status`; disabling weekly must leave the daily timer alone. No generic multi-frequency engine.

B10. Add weekly artifact pruning after `digest_days` while preserving non-content delivery ledger identity; document that very old periods cannot be regenerated and SMTP/DB atomic exactly-once is not promised.

### Phase B automated tests: periods and coverage

- `test_local_monday_period_and_utc_conversion`: correct period week key/time range and usual Tue–Mon artifact selection.
- `test_dst_spring_and_fall`: local weekly boundaries convert to 167-hour and 169-hour UTC spans in America/Toronto as appropriate; never use a fixed 168h delta.
- `test_first_week_without_anchor_fails_closed`: feature enabled midweek, missing anchor -> `insufficient-history`, no model or email call.
- `test_missing_current_monday_daily_fails_closed`: daily job failed/running at weekly cutoff -> no report.
- `test_catchup_daily_run_covers_missed_day`: fewer than seven artifacts but contiguous checkpoint lineage -> report permitted.
- `test_broken_checkpoint_chain_fails_closed`: missing/overlapping changes -> no report.
- `test_other_workflow_artifacts_not_selected`: isolate `global-ps` from `tests` and other group data.
- `test_source_after_period_cutoff_not_selected`: a Monday-late/Tues catch-up does not silently contaminate prior period.
- `test_empty_successful_week_sends_no_material_report`: completeness is separate from materiality.
- `test_manual_explicit_replay_does_not_change_weekly_sources`: replay and dry-run ignored.
- `test_weekly_period_validation`: reject non-Monday, future or disabled workflow period.

### Phase B automated tests: AI and evidence

- `test_daily_items_create_deterministic_card_ids`: repeatable, collision-free card mapping with underlying provenance.
- `test_multiple_daily_cards_merge_one_topic`: fake model output cites several daily cards and passes.
- `test_weekly_rejects_unknown_card_and_wrong_section`: fail before email.
- `test_weekly_rejects_uncited_contact`: names must come from cited evidence, not any other day.
- `test_weekly_rejects_invented_url`: source-anchored URL enforcement.
- `test_weekly_rejects_malformed_json_and_oversize`: fail with recorded stage.
- `test_weekly_prompt_explicitly_preserves_uncertainty`: snapshot/expected prompt rules; later unverified suggestion is not promoted to a fix.
- `test_weekly_provider_hermes_and_ollama`: mock both paths; ensure correct schema/provider and no silent fallback.
- `test_weekly_input_budget_exceeded_does_not_prune_sources`: explicit error, no email.
- `test_prompt_injection_daily_card_treated_as_data`: fake malicious card cannot add recipient or commands.

### Phase B automated tests: delivery and operational safety

- `test_weekly_inherits_to_cc_bcc_and_never_attaches_raw`: fake SMTP captures envelope/headers and attachments.
- `test_weekly_dry_run_has_no_delivery_or_ledger`: rendered preview only; daily checkpoint unchanged.
- `test_weekly_delivered_period_skipped_on_second_run`: no second AI invocation or email under normal retry.
- `test_concurrent_weekly_runs_serialized_by_parent_lock`: no duplicate normal sends.
- `test_weekly_model_validation_smtp_errors_are_retryable`: failure recorded, no delivered ledger; daily unaffected.
- `test_weekly_does_not_touch_daily_checkpoint`: compare before/after state on success, failure and dry-run.
- `test_scheduled_units_install_disable_cleanup`: daily timer unchanged; weekly timer appears only while opted in; existing stale cleanup remains correct.
- `test_weekly_status_displays_period_and_last_success`: no message text leaks into status or logs.
- `test_weekly_retention_nulls_json_but_preserves_delivery_key`: no retention-induced duplicate sends.
- `test_subject_period_stable_across_retry`: stable period identity; different run IDs allowed.

### Phase B DoD

- [ ] Full prior and new automated suites pass; no real network/model/SMTP required for unit tests.
- [ ] One model invocation at most per eligible weekly attempt; selected evidence contains **only** successful canonical daily artifacts.
- [ ] Weekly coverage gate fails closed for missing start, missing Monday completion or broken checkpoint chain.
- [ ] Every weekly output item cites selected card IDs and is checked for supported contacts and URLs.
- [ ] Weekly email has a distinct subject and period, inherits intended recipients/BCC, has no raw attachment and does not modify daily checkpoints.
- [ ] Re-running an already delivered week returns `already-delivered` without SMTP.
- [ ] Weekly timer installs/disables cleanly without changing existing daily timer or collector.
- [ ] Native Ollama and Hermes both use their configured providers safely, with no silent fallback.

## 5. Phase C — Validation, quality review and rollout

### Developer / CI checks

Run from the feature worktree's virtualenv:

```bash
python -m pytest -q tests/test_v2_*.py
python -m whatsapp_digest.cli --config ./config.yaml config validate
python -m whatsapp_digest.cli --config ./config.yaml schedule status
```

`config.yaml` on the live host is local and Git-ignored: **do not overwrite it**. Use a sanitized temporary config/database for development and simulated tests. CI should run in a clean environment with fixture data only.

### Deterministic end-to-end fixture

Build one sanitized 7-day test period with:

1. An anchored previous-Monday success; Tue–Mon canonical daily artifacts, one or more empty.
2. A technical issue first raised on Tuesday, a candidate workaround on Wednesday, lab-tested on Friday (still not represented as a production resolution).
3. A technical question unresolved Thursday but explicitly answered Sunday; it must **not** appear as open in the weekly email.
4. An explicit owner/action with no recorded completion; it stays in follow-ups, correctly attributed.
5. Several daily cards describing the same topic; weekly must synthesize **one** coherent item.
6. An important unrelated technical finding; it must not be lost.
7. Valid technical URLs and a contact list; no invented references/owners.

Use a deterministic fake AI response for CI. Then, separately, perform a controlled **real weekly AI dry-run** once persisted production artifacts cover a full week; manually inspect result quality. A human review is necessary because structural validation cannot detect every misleading technical inference.

### Quality acceptance checklist

- [ ] The output contains a clear practical technical takeaway, not a day-by-day chat transcript.
- [ ] Same issue across days consolidated; no materially duplicated topic between sections.
- [ ] Latest **confirmed** status is correct; proposals are not reported as successful fixes.
- [ ] Questions answered later in the week are removed from open items; genuinely open questions remain only if supported.
- [ ] Every named person, version, technical setting and URL matches at least one cited daily card.
- [ ] Follow-ups are **only** explicit assignments/requests/commitments from the evidence.
- [ ] Important technical facts are preserved; low-value chatter does not dominate.
- [ ] Every item has a valid local evidence trail; no facts are fabricated to fill a section.
- [ ] The report reads naturally in ~3–5 minutes for a normal activity week; shorter if little material.
- [ ] At least one agent/reviewer comparison against the seven daily source digests is documented before enabling real weekly SMTP.

### Safe rollout sequence

1. Create feature branch/worktree from `main` at v2.4.0; capture the baseline regression test result. Do **not** deploy experimental code directly in the active checkout.
2. Complete Phase A and Phase B with fixture tests, migration checks and code review. Keep weekly disabled in current `config.yaml` during development.
3. Prepare a backup of the existing SQLite DB and local config using standard owner-only permissions; preserve current daily units.
4. Merge/release v2.5 only after all tests pass; deploy intentionally to the `m920q` editable install. Run config validation and verify existing daily services/timers.
5. Enable `weekly_summary` on the intended Global PS workflow only, with Monday 08:30. Install schedules once and confirm that daily 07:30 and new weekly 08:30 timers both appear.
6. Begin collecting real validated daily JSON artifacts. Until an anchored full week exists, expect a documented `insufficient-history` skip. **Do not force a partial report or backdate artifacts**.
7. Once the gate reports a complete week, run `digest weekly global-ps --dry-run`, inspect saved preview/coverage, contacts, topics and citations. Ensure no email or checkpoint change.
8. Perform an explicitly approved real weekly email test (correct To/Cc/Bcc) and inspect HTML in an email client. Verify the ledger marks exactly the intended period and a repeat invocation returns `already-delivered`.
9. Monitor the next scheduled Monday: daily succeeds first; weekly runs after; `digest status` shows completion; inspect logs for source counts and non-sensitive errors.
10. Tag/release `v2.5.0` with updated `README.md`, `HANDOFF.md`, sample config and rollback notes.

### Rollback

- Disable `weekly_summary.enabled`, run `digest schedule install`, confirm only the optional weekly timer is removed; **daily timer/collector stay active**.
- Revert to v2.4 code only after ensuring the installed units still point to a supported CLI. Added SQLite tables can safely remain unused; **do not delete historical message/checkpoint data**.
- If upgraded code fails during daily processing, first disable the weekly feature and verify daily behavior; restore the database from a safe backup only when actually required.

## 6. Final release DoD / merge gate

- [ ] Changes restricted to additive weekly feature plus necessary small reusable refactors; no duplicate WhatsApp workflow or model agent framework.
- [ ] Clean v2.4 regression result, all new unit/integration tests, and config/DDL migration tests recorded.
- [ ] Daily run sends exactly as before; checkpoints, history lookback, raw retention, BCC, failures and dry-runs unchanged.
- [ ] Weekly input and output have deterministic, locally inspectable provenance.
- [ ] Period selection handles timezone/DST, empty days, missed daily runs with later catch-up, and missing/incomplete coverage.
- [ ] Failures do not advance checkpoints or mark weekly delivered; normal reruns of delivered periods do not resend.
- [ ] Weekly mail is useful, technically accurate in manual fixture review and not a concatenation of daily reports.
- [ ] No leaked raw WhatsApp content, credentials or message text in logs, tests or committed artifacts.
- [ ] Configuration and `schedule install/remove/status` work on a host with an existing v2.4 installation.
- [ ] README + config example + architecture doc + implementation/testing doc updated; operator commands and limitations explicit.
- [ ] Live weekly SMTP enabled only after a successful full-week dry-run and recipient verification.

## 7. Effort and scope controls

**Estimated complexity: moderate (about 4–5/10)**. The model prompt is straightforward; careful work is required for atomic daily artifact persistence, provenance validation, completeness and timer idempotency. Implement in **two coding phases** (A and B), followed by **one guarded rollout phase** (C). These are checkpoints, not separately versioned releases.

**Do not add** automatic retries beyond ordinary manual retry/Persistent systemd behavior, distributed locks, a new database, multi-week report hierarchies, weekly raw-message attachments, optional weekly recipient matrices, or semantic clustering infrastructure unless real production evidence demonstrates a need. A missed weekly run due to a failed daily run should fail clearly and remain operator-retryable rather than send an incomplete digest.

## 8. Explicit deferred items

- Automatic reconstruction when the scheduled Monday daily run was completed only after the frozen weekly cutoff (no silent leakage of Tuesday content).
- Automatic chunking or map-reduce summarization for unexpectedly oversized weekly input.
- Multi-group weekly aggregation, richer trend scoring, delivery-provider-level exact-once semantics, and UI history browsing.

These are not prerequisites for v2.5.0.

## 9. Validation record — 2026-11-04

**Branch/worktree:** `feature/v2.5.0-weekly-digest`,
`/home/egori4/scripts/whatsapp-tech-digest-v2.5.0`.
Production `main` checkout, `config.yaml`, SQLite and systemd timers stayed unchanged.

- Baseline prior to feature changes: **144 v2 tests passed**.
- Full feature branch test suite after Phase A/B: **197 passed**
  (144 original + 53 new weekly, database, config, provider, scheduling,
  BCC, SMTP-envelope and failure/retry tests).
- `python -m compileall -q whatsapp_digest` and `git diff --check`: passed.
- Real Hermes integration: synthetic offline-fixture sources representing
  a contiguous Oct 26-Nov 2 period were persisted in a private `/tmp` SQLite
  DB and passed the real `digest weekly global-ps --period 2026-10-26 --dry-run`
  path with the installed **openai-codex / gpt-6.1-sol / medium** model.
- Result: coherent AppWall wildcard learning including apex caveat and pending
  KB commitment, DPX10 5-Gbps answer from a later card (not unresolved),
  SecurePath Bot Manager guidance, and Alteon handshake workaround correctly
  described as **lab verified but not production resolved**. Citations were
  rendered as readable daily digest dates; input evidence count was seven.
- All validation settings were isolated: test To was `smoke@example.com`,
  CC/BCC empty, SMTP endpoint deliberately not available, CLI `--dry-run`.
  Other tests used an in-memory fake SMTP with fake addresses only. **No David
  recipient, real SMTP send, source WhatsApp history or production DB was used.**
- The tested artifact path under `/tmp/v25-weekly-smoke-*` is a temporary
  private test output; it must not be copied into Git or used as live history.

**Remaining before production release:** feature-branch review/merge decision,
installation on the production checkout, explicit enablement of weekly on the
real Global PS workflow, one full anchored week of daily artifacts, live-data
quality comparison, SMTP recipient approval and real scheduled-operation
observation. Do not mark the release/deployment gate complete based solely on
synthetic evidence.
