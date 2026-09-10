# Daily production scheduler design

## Status

This is a source-stage design. The repository contains systemd-unit builders and a tested owner-only credential bridge (`UNATTENDED_DELIVERY_STAGE.md`). Repository contents do not assert the current local systemd activation state; inspect the installed user units and timer directly before relying on a schedule.

The staged production policy selects the dedicated owner-only state directory `data-production/`; its spool database must be created only by the collector after a separately approved activation restart. R3, R4, and R5 remain preserved test evidence and must never be selected by the production policy.

## Intended cadence

- Wall-clock schedule: **08:00 every day**
- Time zone: **America/Toronto**
- Recipient: existing fixed policy recipient
- Model path: Hermes Codex one-pass, with explicit policy-pinned reasoning/output bounds

The policy retains `activate_only_after_dst_contract=true`. Before any timer activation, the final timer implementation must prove that both Toronto DST transition probes resolve to 08:00 local time.

## Delivery modes

### Recommended: scheduled render, separately approved exact delivery

At 08:00, an inactive future timer would invoke `render-only` and write an owner-only dated artifact plus review manifest. It must not contact SMTP, create trusted delivery provenance, or advance the checkpoint. After human review, a separately approved `delivery-capable --deliver-reviewed-artifact` action sends only the exact manifest-bound bytes.

This preserves the tested reviewed-artifact security boundary.

### Not staged: unattended automatic email

An unattended `delivery-capable` timer could generate and submit a digest without a human review of the exact artifact. That does not satisfy the reviewed-artifact gate used by R5. It is deliberately not implemented or enabled by this design.

If Egor chooses unattended delivery later, it requires a new explicit policy decision that authorizes that reduced review boundary, a dedicated protected credential-loading wrapper, focused tests, a separate source review/commit, DST verification, and a separate activation approval.

## Required activation sequence

1. Read back the staged production policy and its exact dedicated spool path.
2. Separately approve and perform one gateway restart; verify the production spool is initialized and the collector safeguards remain live.
3. Confirm representative production intake behavior with no WhatsApp output, read receipt, typing, model run, or SMTP action.
4. Validate the chosen systemd timer's DST behavior for America/Toronto without enabling it.
5. Stage the timer and owner-only runtime wrapper; inspect its command, policy path, working directory, credential boundary, and next wall-clock run.
6. Obtain separate approval to enable the timer.
7. After its first run, verify artifact/delivery outcome, checkpoint/provenance, and no unexpected WhatsApp side effect.

## Credential boundary

The SMTP password remains outside this repository, policy, timer arguments, and logs. The previously approved SMTP reuse source may be supplied only ephemerally to a child process after non-secret transport compatibility and unique mapping checks. No unit or wrapper may print, copy, persist, or expose the credential.
