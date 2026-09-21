# Unattended Delivery Stage — Inert Artifact

## Status

This source tree contains a systemd-unit generator and an owner-only SMTP credential bridge. The source tree itself does **not** create a user unit, timer, virtual environment, credential file, model run, SMTP connection, email, or scheduler activation.

The live production policy remains outside this repository. Runtime installation and enablement are external state and must always be read back from the local user systemd manager.

## Intended runtime contract

- Daily schedule: `07:30` in `America/Toronto` via systemd `OnCalendar`.
- Missed-run behavior: `Persistent=false`; the service must not send a late catch-up digest after downtime.
- Recipient and transport: fixed only by the protected policy. The service exposes no recipient or SMTP-host override.
- Secret bridge: reads exactly one named password from an existing owner-only credential source, maps it only to the policy's SMTP password reference for the process lifetime, then restores the prior environment. It does not copy, write, log, or import other source variables.
- Execution: the only runner mode is `delivery-capable`; that mode may invoke Hermes Codex and SMTP **only after an explicitly approved timer activation**.
- Failure: missing/weak/malformed credential sources fail closed with exit code `78` and no policy, recipient, source-path, or secret diagnostic.

## Source-level checks

The focused tests cover:

1. exact one-secret mapping and environment cleanup;
2. refusal of a group/world-readable source before the runner is called;
3. refusal of a missing source key before the runner is called;
4. timer/service content: Toronto wall-clock scheduling, no late catch-up, no generic `EnvironmentFile`, restrictive umask, and systemd hardening; and
5. exact fixed delivery runner arguments with no recipient override.

## Separate activation gate — not authorized by this artifact

Before installing or enabling anything, perform a fresh read-only preflight:

1. verify the next two `systemd-analyze calendar` occurrences for `07:30 America/Toronto`, including DST-transition evidence;
2. verify the dedicated production spool received a post-restart non-owner group event and its owner-only permissions;
3. verify the selected Python runtime can import the checked-out package and resolve the Hermes CLI through the intended user profile;
4. compare non-secret SMTP host/port/sender policy fields against the approved reuse source and verify a one-to-one source-key mapping without printing any secret;
5. generate units to a temporary owner-only directory and run `systemd-analyze verify` before copying them into the user-unit directory;
6. obtain explicit approval to install the inert units; read them back without enabling; then obtain a distinct explicit approval to enable the timer; and
7. after the first scheduled run, verify result, checkpoint/provenance, and absence of unintended WhatsApp output.

No live SMTP probe is included here. A real relay acceptance test is an operational side effect and requires its own explicit approval.
