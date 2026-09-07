# Public Project Handoff

## Repository status

This repository contains the source code, tests, and sanitized examples for a collector-only WhatsApp technical-digest pipeline. It intentionally excludes all operational state.

Not present in Git:

- WhatsApp group directories, JIDs, participant information, or message bodies;
- active policies, SMTP credentials, OAuth material, bridge sessions, or local configuration;
- SQLite spools, rendered digests, review manifests, logs, locks, and rollback backups;
- personal paths and production service identities.

## Safety model

- The collector is never a conversational bot.
- A deployment must pin exactly one immutable group JID in an owner-only, Git-ignored policy. Display-name matching, wildcards, and aliases are prohibited.
- The outbound guard must deny every WhatsApp output primitive, including unknown POST routes.
- The bridge and local model endpoints must remain loopback-only.
- The runner defaults to `validate-only`; rendering, reviewed-artifact delivery, service restart, and scheduling remain separate operator approvals.
- SMTP delivery must use the fixed recipient declared in the protected local policy. The published code deliberately contains no recipient identity or credentials.

## Publication-safe setup

1. Copy `config/digest.policy.example.json` to the ignored `config/digest.policy.json`.
2. Supply the real immutable target JID, allowlist, local state path, SMTP transport, and recipient only in that owner-only local policy.
3. Configure the Hermes gateway’s matching allowlist and collector target through the deployment guide, using the same JID.
4. Run the test suite and static checks before any activation.
5. Keep all runtime files ignored. Do not commit policy files, group metadata, SQLite databases, render artifacts, or credentials.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q
git diff --check
```

These checks do not send a message, invoke a model, send email, restart a service, or activate a schedule.

## Local operator note

Detailed production evidence and rollback records belong in an owner-only operational handoff outside version control. They must never be copied into this repository.
