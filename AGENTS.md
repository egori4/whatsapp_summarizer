# Agent Context — WhatsApp Tech Digest

Read `HANDOFF.md`, `README.md`, and the local deployment documentation before making changes. This repository is intentionally publication-safe: live runtime details belong only in ignored owner-only files.

## Scope and boundaries

- This is a **collector-only** integration, never a conversational WhatsApp bot.
- Pin exactly one immutable group JID in the owner-only local policy. Never target by display name, alias, wildcard, or bare phone number.
- Do not print, commit, or copy policies, group directories, JIDs, participant identities, message content, SQLite spools, rendered digests, review manifests, logs, backups, OAuth material, SMTP credentials, or session data.
- Keep the bridge and optional local model service loopback-only.
- Use the Hermes local CLI credential abstraction. Never extract tokens.
- Do not send WhatsApp output, mark messages read, type, invoke a model, send SMTP, activate a scheduler, change a live policy, or restart a service without an explicit operator approval for that exact action.

## Configuration contract

- `config/digest.policy.example.json` is nondeployable and contains placeholders only.
- The active `config/digest.policy.json`, group directory, runtime state, and lock files are ignored by Git and must be owner-only.
- The protected policy and Hermes gateway configuration must use the same sole JID allowlist and collector target.
- SQLite state is permanently target-JID bound. Changing targets requires a fresh empty state directory; never reuse a spool for a different target.
- A delivery recipient is fixed by the protected policy. The application must use `policy.recipient`; it must not embed a personal recipient in source or accept a CLI override.

## Development discipline

1. Read-only preflight before edits.
2. Add a failing regression test before behavior changes.
3. Keep runtime data outside Git.
4. Run `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q` and `git diff --check` before completion.
5. Run replay only against an isolated owner-only spool in `render-only` mode.
6. Preserve evidence until explicit cleanup approval.
7. Update the public handoff only with sanitized, reusable facts. Store operational evidence outside version control.
8. After a configuration write, read back the exact affected target without exposing it in shared output.

## Digest contract

- Actionable `one_pass` and `two_call` modes use the policy-pinned Hermes Codex local adapter, with explicit reasoning and bounded prompt/output sizes.
- Preserve source-grounded technical questions, commands, versions, repositories, and URLs.
- Separate unresolved technical questions from answered topics.
- Do not invent conclusions or upgrade informal guidance into confirmed documentation.
- Strict validation, provenance, exact reviewed-artifact delivery, and checkpoint rules fail closed.
