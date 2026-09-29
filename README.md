# WhatsApp Technical Digest v2

A read-only WhatsApp group collector and scheduled AI digest pipeline.

It continuously collects messages from explicitly configured WhatsApp groups, stores them temporarily in SQLite, summarizes pending messages with Hermes, validates the model output, emails the digest, and advances a per-workflow checkpoint only after a successful outcome.

It is **not** a conversational WhatsApp bot and it never sends messages back to WhatsApp.

## Table of contents

- [Status](#status)
- [Architecture](#architecture)
- [Safety model](#safety-model)
- [Prerequisites](#prerequisites)
- [Initial installation](#initial-installation)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [Dry-runs](#dry-runs)
- [Run statuses](#run-statuses)
- [Routine operations](#routine-operations)
- [Changing configuration](#changing-configuration)
- [Updating, switching branches, and applying changes](#updating-switching-branches-and-applying-changes)
- [Troubleshooting](#troubleshooting)
- [Data and retention](#data-and-retention)
- [Functional limits of v2](#functional-limits-of-v2)
- [Tests](#tests)
- [Shutdown / rollback](#shutdown--rollback)
- [What “done” means for this project](#what-done-means-for-this-project)

## Status

**Version:** 2.1.0
**Runtime:** Linux + systemd user services  
**Python:** 3.11+  
**Model gateway:** Hermes  
**WhatsApp transport:** unmodified Hermes Baileys bridge running standalone on loopback  
**Delivery:** SMTP email

The implementation is feature-complete for the v2 scope and is in production-pilot/quality-observation mode. The remaining work is operational observation of real digests, not additional architecture.

For design rationale and security boundaries, see [V2_ARCHITECTURE_SPEC.md](V2_ARCHITECTURE_SPEC.md).
For the current engineering state, decisions, quality evidence, and next steps, see [HANDOFF.md](HANDOFF.md).

> Some older files in this repository describe the previous v1 architecture. For v2 deployment and operations, this README and `V2_ARCHITECTURE_SPEC.md` are authoritative.

---

## Architecture

```text
WhatsApp
   |
   v
whatsapp-digest-bridge.service
  127.0.0.1:3001
  configured groups only
   |
   v
whatsapp-digest-collector.service
   |
   v
SQLite
   |
   v
per-workflow systemd timer
   |
   v
Hermes model worker
   |
   v
validate -> render -> SMTP email
   |
   v
checkpoint
```

Hermes may continue running its normal gateway for Telegram or other integrations, but the Hermes gateway's own WhatsApp adapter must remain disabled. This prevents two consumers from competing for the same WhatsApp session/message queue.

---

## Safety model

The v2 design intentionally keeps WhatsApp content out of normal Hermes conversational dispatch.

Key guarantees:

- only configured WhatsApp group JIDs are admitted by the supervised bridge;
- direct messages are ignored by the collector;
- the collector only performs `GET /messages` against the bridge;
- the application has no WhatsApp send/edit/typing path;
- WhatsApp source text is treated as untrusted data, never as instructions;
- model execution is one-shot with tools, memory, and context files disabled;
- email recipients come only from trusted `config.yaml`;
- model output is schema-validated before rendering or delivery;
- a failed model/validation/delivery run does not advance the checkpoint;
- dry-runs never email and never advance the checkpoint;
- processed raw messages are retained only for the configured retention period;
- pending messages are never removed by retention;
- logs contain operational metadata/counts, not WhatsApp message bodies.

The standalone Hermes bridge binary exposes outbound endpoints because it is an upstream component, but v2 never calls them.

---

## Prerequisites

1. Linux with systemd and a working user systemd manager.
2. Python 3.11 or newer.
3. Hermes installed under `~/.hermes`.
4. Hermes WhatsApp bridge dependencies installed.
5. A paired WhatsApp session.
6. An SMTP account/relay.
7. The repository checked out on the v2 code.

The transport installer automatically discovers:

- Hermes bridge script under `~/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js`;
- Hermes bundled Node.js under `~/.hermes/tools/node-*/bin/node`;
- paired session under either:
  - `~/.hermes/whatsapp/session`, or
  - `~/.hermes/platforms/whatsapp/session`.

If WhatsApp has never been paired, use the supported Hermes pairing flow first:

```bash
hermes whatsapp
```

After pairing, disable WhatsApp in the normal Hermes gateway as described below.

---

## Initial installation

### 1. Clone/check out the v2 branch

```bash
git clone https://github.com/egori4/whatsapp_summarizer.git
cd whatsapp_summarizer
git checkout main
```

### 2. Create the virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

`pip install -e .` installs the project in **editable mode**. The virtual environment records this checkout as the installed `whatsapp-tech-digest` package, so `digest` imports Python code directly from the working tree instead of from a copied package. This means switching branches or editing Python source changes what the next `digest` process runs. Re-run `pip install -e .` when dependencies or package metadata/version change, or after recreating the virtual environment.

Validate the code:

```bash
pytest -q tests/test_v2_*.py
```

### 3. Create the configuration

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with the real group JIDs, SMTP settings, recipients, workflow instructions, schedules, and model settings.

Validate it:

```bash
digest --config ./config.yaml config validate
```

Older valid configs continue to run with built-in defaults for newly introduced optional settings. If validation reports optional defaults that are not explicitly present, generate a reviewable upgraded copy:

```bash
digest --config ./config.yaml config upgrade
```

This writes `config.yaml.upgraded` and never modifies the active file. Existing values always win; only missing known defaults are added. Review the generated file, then apply deliberately with:

```bash
digest --config ./config.yaml config upgrade --apply
```

`--apply` validates the generated configuration, creates a numbered backup such as `config.yaml.bak` / `config.yaml.bak.1`, and then replaces the active file. Re-running upgrade after all defaults are present is a no-op. Because PyYAML writes the upgraded copy, comments and hand formatting may be normalized even though configuration values are preserved.

### 4. Disable WhatsApp inside the normal Hermes gateway

The v2 project owns the dedicated WhatsApp bridge. The normal Hermes gateway must not also start a WhatsApp bridge.

Check:

```bash
grep '^WHATSAPP_ENABLED=' ~/.hermes/.env
```

Set:

```text
WHATSAPP_ENABLED=false
```

If Hermes gateway is installed as the system service used on this host:

```bash
sudo systemctl restart hermes-gateway.service
```

Verify no old Hermes WhatsApp listener remains:

```bash
ss -ltnp | grep ':17778\b' || true
```

Do not run the normal Hermes WhatsApp adapter and the v2 bridge at the same time.

### 5. Configure the SMTP secret for scheduled runs

The SMTP password is not stored in `config.yaml`. The config contains only the environment-variable name, for example:

```yaml
email:
  password_env: WHATSAPP_DIGEST_SMTP_PASSWORD
```

The generated scheduled services load `digest.env` from the project directory.

Create it with owner-only permissions:

```bash
read -rsp "SMTP password: " SMTP_SECRET
echo
printf 'WHATSAPP_DIGEST_SMTP_PASSWORD=%s\n' "$SMTP_SECRET" > digest.env
unset SMTP_SECRET
chmod 600 digest.env
```

`digest.env` is Git-ignored.

For manual `digest email test` or manual delivery-capable `digest run` commands, the password variable must also exist in that shell environment.

### 6. Install the supervised WhatsApp transport

```bash
digest --config ./config.yaml transport install
```

This generates and enables:

- `whatsapp-digest-bridge.service`
- `whatsapp-digest-collector.service`

The bridge service automatically gets:

```text
WHATSAPP_GROUP_POLICY=allowlist
WHATSAPP_GROUP_ALLOWED_USERS=<all workflow group_jids from config.yaml>
```

The installer:

- refuses to proceed if Hermes gateway WhatsApp is still enabled;
- refuses an unmanaged bridge already responding on the configured endpoint;
- verifies the generated systemd units;
- starts/restarts the managed bridge;
- waits for the bridge to report `connected`;
- starts/restarts the collector;
- verifies both services are active.

Check:

```bash
digest --config ./config.yaml transport status
```

Healthy output should show:

```text
Hermes WhatsApp: disabled
Bridge enabled: enabled
Bridge active: active
Bridge health: connected
Collector enabled: enabled
Collector active: active
User linger: yes
```

Direct verification:

```bash
systemctl --user status whatsapp-digest-bridge.service --no-pager -l
systemctl --user status whatsapp-digest-collector.service --no-pager -l
ss -ltnp | grep ':3001\b'
```

### 7. Enable user lingering

User services should survive logout and start after reboot without waiting for an interactive login.

Check:

```bash
loginctl show-user "$USER" -p Linger
```

If needed:

```bash
sudo loginctl enable-linger "$USER"
```

Expected:

```text
Linger=yes
```

### 8. Test SMTP

Make the password variable available in the current shell, then:

```bash
digest --config ./config.yaml email test <workflow-id>
```

Example:

```bash
digest --config ./config.yaml email test tests
```

The command prints the effective SMTP configuration without printing the password.

SMTP acceptance means the relay accepted the message; it does not guarantee inbox placement. If accepted mail is missing, check spam/quarantine, relay logs, bounces, or the provider's message trace.

### 9. Install workflow schedules

```bash
digest --config ./config.yaml schedule install
```

Check:

```bash
digest --config ./config.yaml schedule status
systemctl --user list-timers 'whatsapp-digest-*'
```

A healthy scheduled workflow should be `enabled`, `active`, have a real `Next` time, and report `Health: healthy`.

Systemd may display the next activation in UTC even though the calendar is configured with the workflow timezone. The configured timezone remains authoritative and DST is handled by systemd.

### 10. Initialize each workflow explicitly

A new workflow starts as:

```text
NEEDS_INITIAL_RUN
```

The scheduler will **not** guess a starting point.

Choose one explicit initial window.

Examples:

Start with the last 24 hours already present in SQLite:

```bash
digest --config ./config.yaml run global-ps --last 24h
```

Start at a specific local time:

```bash
digest --config ./config.yaml run global-ps --since "2026-09-28 00:00"
```

A timestamp without an offset is interpreted in that workflow's configured timezone.

Start fresh from approximately now:

```bash
digest --config ./config.yaml run global-ps --since "$(date -Iseconds)"
```

Important: `--last` and `--since` query the **local SQLite database only**. They do not retrieve WhatsApp history. Messages sent before the v2 bridge/collector collected them cannot be backfilled.

A real initial run with zero matching rows still initializes the workflow at the current collected cutoff. This is the intended way to establish a clean baseline.

Check:

```bash
digest --config ./config.yaml status global-ps
```

Expected after initialization:

```text
State: READY
```

---

## Configuration

See [config.example.yaml](config.example.yaml) for a complete example.

### Global paths

```yaml
paths:
  database: ./data/messages.db
  log: ./logs/digest.log
```

Relative paths are resolved relative to `config.yaml`.

### Retention

```yaml
retention:
  processed_raw_days: 7
  log_days: 30
```

After successful real runs, processed messages older than `processed_raw_days` are removed. Pending messages are never purged by retention.

Log files use configured log retention.

### Hermes model gateway

```yaml
hermes:
  command: hermes
  timeout_seconds: 600
```

The model worker uses the installed Hermes runtime without modifying Hermes core.

Per workflow:

```yaml
model:
  provider: inherit
  name: inherit
  reasoning: inherit
```

Overrides may be set when supported by the installed Hermes runtime.

The run output records the actual provider, model, and reasoning used.

#### Switching models

The digest does not maintain its own provider registry. Model selection must match the installed Hermes configuration on the host. The authoritative file is normally:

```bash
~/.hermes/config.yaml
```

Inspect it with:

```bash
less ~/.hermes/config.yaml
```

The important sections are:

```yaml
model:
  default: gpt-6-sol
  provider: openai-codex

providers:
  ollama-local:
    api: http://127.0.0.1:11434/v1
    transport: chat_completions
    models:
      - qwen3.5:9b
      - qwen3.5:4b

model_aliases:
  local-qwen9b:
    model: qwen3.5:9b
    provider: ollama-local
  local-qwen4b:
    model: qwen3.5:4b
    provider: ollama-local
```

To use the Hermes default model/provider, keep the workflow inherited:

```yaml
model:
  provider: inherit
  name: inherit
  reasoning: inherit
```

To use a named local Ollama provider, set the workflow to the **provider name under `providers:`** and the **real model name**, for example:

```yaml
model:
  provider: ollama-local
  name: qwen3.5:9b
  reasoning: inherit
```

For the smaller local model:

```yaml
model:
  provider: ollama-local
  name: qwen3.5:4b
  reasoning: inherit
```

The digest currently does **not** resolve Hermes `model_aliases`. Aliases such as `local-qwen9b` are convenient for interactive Hermes commands such as `/model local-qwen9b`, but the digest workflow should use the concrete pair:

```text
provider: ollama-local
name: qwen3.5:9b
```

Do not use this combination for the named provider above:

```yaml
model:
  provider: custom
  name: local-qwen9b
```

Bare `custom` does not identify which named custom provider/endpoint Hermes should use, and the alias is not expanded by the digest worker. A typical failure is:

```text
provider 'custom' resolved without credentials
```

For another local model/provider, use the same rule:

1. Confirm the model is installed, for example with `ollama list`.
2. Confirm the corresponding named provider and endpoint under `providers:` in `~/.hermes/config.yaml`.
3. Use that provider key and the concrete model name in the workflow `model:` block.
4. Validate the YAML and perform a dry run before using it in a scheduled digest.

Example verification:

```bash
digest --config ./config.yaml config validate
digest --config ./config.yaml run global-ps --last 2h --dry-run
```

`config validate` checks the digest configuration shape; the dry run is what verifies that Hermes can actually resolve and call the selected provider/model. For slower local models, start with a small window such as `--last 2h` before testing a larger source window.

### WhatsApp

```yaml
whatsapp:
  bridge_url: http://127.0.0.1:3001
  poll_interval_seconds: 1
```

The production deployment expects the bridge on loopback.

### Email

```yaml
email:
  host: smtp.example.com
  port: 587
  sender: digest@example.com
  username: digest@example.com
  password_env: WHATSAPP_DIGEST_SMTP_PASSWORD
  starttls: true
  timeout_seconds: 30
```

Workflow delivery:

```yaml
delivery:
  email:
    to:
      - user@example.com
    cc: []
    attach_raw_messages: true
    subject: "[TechTeam Daily] {date} — {workflow_name}"
```

Supported subject fields:

- `{date}`
- `{workflow_name}`
- `{workflow_id}`
- `{run_id}`

Recipients are fixed by config; WhatsApp/model content cannot change them.

### Schedules

Daily:

```yaml
schedule:
  type: daily
  at: "08:00"
```

Weekly:

```yaml
schedule:
  type: weekly
  at: "08:00"
  days: [mon, wed, fri]
```

Monthly:

```yaml
schedule:
  type: monthly
  at: "08:00"
  day: 1
```

The workflow timezone defaults to `defaults.timezone` and may be overridden in the workflow schedule.

Omit `schedule` for manual-only workflows.

### Historical context

By default, normal checkpoint-driven runs include up to 48 hours of already processed messages as read-only context:

```yaml
context:
  enabled: true
  lookback: 48h
  max_messages: 100
```

Historical context helps resolve continuations and short replies across digest boundaries. It never drives checkpointing and every emitted digest item must cite at least one current message. Set `enabled: false` to disable it. The lookback cannot exceed `retention.processed_raw_days`. Explicit `--last`/`--since` runs do not add extra historical context.

### Summarization

Each workflow may provide trusted instructions and optional sections:

```yaml
summarization:
  instructions: |
    Focus on technical problems, troubleshooting, root cause, fixes,
    versions, workarounds, useful commands and links, important
    administrative announcements, explicit actions, and unanswered questions.
  sections:
    - id: technical_updates
      title: Technical Updates
      guidance: Technical issues, fixes and guidance.
```

Configured sections are optional. Empty sections are omitted. If no sections are configured, the model may use validated dynamic headings.

---

## CLI reference

| Command | Purpose |
| --- | --- |
| `digest --config ./config.yaml config validate` | Validate configuration and fail on invalid values. |
| `digest --config ./config.yaml status` | Show state/pending count for all workflows. |
| `digest --config ./config.yaml status <workflow>` | Show one workflow. |
| `digest --config ./config.yaml whatsapp groups` | List group metadata previously discovered by the collector. |
| `digest --config ./config.yaml collector once` | Poll `/messages` once and print counts. Mainly diagnostic. |
| `digest --config ./config.yaml collector run` | Run collector in foreground. Normally systemd owns this. |
| `digest --config ./config.yaml email test <workflow>` | Send a source-free SMTP test using the workflow recipients. |
| `digest --config ./config.yaml run <workflow> --last 24h --dry-run` | Generate/validate preview without email or checkpoint change. |
| `digest --config ./config.yaml run <workflow> --since <time> --dry-run` | Preview an explicit initial/manual window. |
| `digest --config ./config.yaml run <workflow>` | Process pending changes for an initialized workflow. |
| `digest --config ./config.yaml run <workflow> --last 24h` | Real explicit-window run; useful for initialization/manual replay of collected rows. |
| `digest --config ./config.yaml transport install` | Generate/reinstall/restart managed bridge and collector services. |
| `digest --config ./config.yaml transport status` | Show Hermes WhatsApp state, bridge/collector state, health and linger. |
| `digest --config ./config.yaml transport remove` | Stop/disable/remove v2 bridge and collector units. |
| `digest --config ./config.yaml schedule install` | Generate/update/enable workflow timers. |
| `digest --config ./config.yaml schedule status` | Show timer schedule, enabled/active state, next activation and health. |
| `digest --config ./config.yaml schedule remove` | Stop/disable/remove generated digest timers/services. |

`--scheduled` is an internal flag used by generated systemd services and is not intended for normal operator use.

---

## Dry-runs

Example:

```bash
digest --config ./config.yaml run tests --last 2d --dry-run
```

A successful dry-run prints the text digest and writes owner-only artifacts under:

```text
<data-directory>/dry-runs/<run-id>/
  digest.txt
  digest.html
  raw_messages.txt
```

The raw file is the exact sanitized source window supplied to the model.

Dry-runs:

- may call the model;
- never send email;
- never initialize a workflow;
- never advance a checkpoint.

---

## Run statuses

Common statuses:

| Status | Meaning |
| --- | --- |
| `needs-initial-run` | Scheduled workflow has never been initialized; nothing processed. |
| `success-no-pending` | No pending rows; checkpoint/baseline remains successful. |
| `success-no-usable` | Pending changes existed but none contained usable text/caption. |
| `success-no-material` | Model/validation succeeded but there was nothing worth emailing. Checkpoint advanced. |
| `delivered` | SMTP accepted the material digest and checkpoint advanced. |
| `dry-run-success` | Preview generated successfully; checkpoint unchanged. |
| `dry-run-no-material-updates` | Valid preview contained no material items; checkpoint unchanged. |
| `failed` | Model, validation, freshness, rendering, or delivery failed; checkpoint did not advance. |

`delivered` means accepted by the configured SMTP server, not guaranteed inbox delivery.

There is an unavoidable at-least-once edge case: if SMTP accepts a message and the process crashes before the local success/checkpoint transaction commits, a retry may send a duplicate. The system favors not losing a digest over pretending SMTP provides exactly-once semantics.

---

## Routine operations

### Overall workflow state

```bash
digest --config ./config.yaml status
```

### Transport health

```bash
digest --config ./config.yaml transport status
```

### Scheduler health

```bash
digest --config ./config.yaml schedule status
systemctl --user list-timers 'whatsapp-digest-*'
```

### Application log

```bash
tail -f ./logs/digest.log
```

The log intentionally contains operational metadata such as run IDs, provider/model information, SMTP acceptance, collector counts, and failures. It does not intentionally log WhatsApp message bodies or SMTP passwords.

### systemd journals

```bash
journalctl --user -u whatsapp-digest-bridge.service -n 100 --no-pager
journalctl --user -u whatsapp-digest-collector.service -n 100 --no-pager
journalctl --user -u whatsapp-digest-<workflow>.service -n 100 --no-pager
```

### Confirm listeners

```bash
ss -ltnp | grep -E ':(17778|3001)\b'
```

Normal v2 deployment:

- `3001`: dedicated v2 bridge listening;
- `17778`: no competing Hermes WhatsApp bridge.

---

## Changing configuration

### Config schema upgrades

Application releases may introduce new optional configuration keys. Runtime loading remains backward-compatible by applying safe internal defaults, while `config validate` reports defaults that are not explicitly present in the file.

Use:

```bash
digest --config ./config.yaml config upgrade
```

to create a non-destructive `config.yaml.upgraded` review file. Use `--apply` only after review. The upgrader is **add-missing-only**: it never replaces an existing user value.

### Change instructions/model/recipients

Edit `config.yaml`, validate it, and future runs will read the new values:

```bash
digest --config ./config.yaml config validate
```

No service regeneration is normally required.

### Change schedule

After editing schedule settings:

```bash
digest --config ./config.yaml schedule install
```

### Add/remove/change a WhatsApp group or workflow

After editing workflows/group JIDs:

```bash
digest --config ./config.yaml config validate
digest --config ./config.yaml transport install
digest --config ./config.yaml schedule install
```

Then initialize any new workflow explicitly.

If a logical workflow moves to a completely different WhatsApp group, prefer a **new workflow ID** instead of reusing the old ID, so old checkpoint state cannot be confused with the new source.

---

## Updating, switching branches, and applying changes

The project is installed in editable mode, so source-code changes in the checked-out working tree are picked up by the next `digest` process. You do **not** normally restart long-running services just because runner/model/rendering Python code changed.

### Switch to another branch for testing

```bash
cd ~/scripts/whatsapp-tech-digest
git switch <branch>
git pull
source .venv/bin/activate
pip install -e .
pytest -q tests/test_v2_*.py
digest --config ./config.yaml config validate
# If optional defaults are reported:
digest --config ./config.yaml config upgrade
```

`pip install -e .` is recommended after a branch switch because the branch may change dependencies or package metadata. If only Python source changed, editable mode already points at the working tree, but reinstalling is cheap and removes ambiguity.

After validation, the next manual or scheduled digest run uses the checked-out branch. Switching the production host's checkout therefore effectively changes the application version used by the next scheduled run.

### Pull updates on the current branch

```bash
cd ~/scripts/whatsapp-tech-digest
git pull
source .venv/bin/activate
pip install -e .
pytest -q tests/test_v2_*.py
digest --config ./config.yaml config validate
# If optional defaults are reported:
digest --config ./config.yaml config upgrade
```

Then apply only the operational actions required by what changed:

| What changed | Required action |
| --- | --- |
| Runner/model/validation/renderer Python code only | No service reinstall; next run uses new code |
| Python dependencies or package metadata/version | `pip install -e .` |
| `config.yaml` instructions/model/recipients/context/SMTP values | `digest ... config validate`; future runs read the new values |
| SMTP password or another variable in `digest.env` | Edit `digest.env`, keep mode `600`; no schedule reinstall is normally required |
| Workflow schedule | `digest ... schedule install` |
| Workflow/group JID added, removed, or changed | `digest ... transport install` and `digest ... schedule install` |
| Transport implementation or generated transport-unit logic | `digest ... transport install` |
| Generated scheduler/service-unit logic | `digest ... schedule install` |
| Virtual environment recreated | `pip install -e .` |

Useful post-update checks:

```bash
digest --config ./config.yaml transport status
digest --config ./config.yaml schedule status
```

Do not routinely reinstall transport or schedules after every code pull. Reinstall only when the relevant generated units, group allowlist, or schedule configuration changed.

### Environment variables

Scheduled services load project-level secrets from `digest.env`. For example:

```text
WHATSAPP_DIGEST_SMTP_PASSWORD=...
```

After changing `digest.env`:

```bash
chmod 600 digest.env
```

The next newly started scheduled digest process reads the updated file. Manual commands still require the same variable in the current shell environment when SMTP authentication is needed.

### Roll back to production `main`

```bash
cd ~/scripts/whatsapp-tech-digest
git switch main
git pull
source .venv/bin/activate
pip install -e .
pytest -q tests/test_v2_*.py
digest --config ./config.yaml config validate
```

As above, reinstall transport or schedules only if the target branch changes those operational definitions.

---

## Troubleshooting

### Timer is enabled but inactive / no NEXT time

```bash
digest --config ./config.yaml schedule status
systemctl --user status whatsapp-digest-<workflow>.timer --no-pager -l
journalctl --user -u whatsapp-digest-<workflow>.timer -n 50 --no-pager
systemd-analyze calendar '<OnCalendar value>'
```

Healthy timers are enabled, active, and have a real next activation.

### Bridge service active but messages do not appear

Check:

```bash
digest --config ./config.yaml transport status
systemctl --user cat whatsapp-digest-bridge.service
journalctl --user -u whatsapp-digest-bridge.service -n 100 --no-pager
```

The generated bridge unit should contain:

```text
WHATSAPP_GROUP_POLICY=allowlist
WHATSAPP_GROUP_ALLOWED_USERS=<configured group JIDs>
```

If config group JIDs changed, rerun:

```bash
digest --config ./config.yaml transport install
```

### `group_policy_rejected`

The incoming group is not admitted by the bridge allowlist. Confirm its exact `@g.us` JID matches a workflow and reinstall transport.

Do not solve this by setting a globally open group policy.

### Collector cannot reach bridge

Typical log:

```text
WhatsApp bridge read failed: ... URLError
```

Check bridge state and port 3001. The collector retries automatically.

### Hermes gateway starts another WhatsApp bridge

Check:

```bash
grep '^WHATSAPP_ENABLED=' ~/.hermes/.env
```

It must be:

```text
WHATSAPP_ENABLED=false
```

Then restart the normal Hermes gateway.

### `0 messages` with `--last`

`--last` does not query WhatsApp servers. It filters rows already captured in SQLite.

If the bridge/collector was not running, or that group was not allowed at the time, those historical messages do not exist in v2 and cannot be recovered by increasing the window.

### SMTP accepted but email is not visible

Check:

- exact To/Cc printed by `email test`;
- spam/junk/quarantine;
- relay/provider message trace;
- bounce messages to the configured sender.

Application SMTP log:

```bash
tail -n 100 ./logs/digest.log
```

### Password works manually but timer fails

The scheduled service does not inherit an arbitrary interactive shell environment. Ensure `digest.env` exists, contains the exact configured `password_env` variable, and is mode 0600.

### Workflow stays NEEDS_INITIAL_RUN

Initialize it manually with `--last` or `--since`. Scheduled execution intentionally refuses to guess the first window.

---

## Data and retention

SQLite stores:

- collected messages/captions;
- group metadata;
- per-workflow checkpoint state;
- run metadata.

It does not serve as a permanent WhatsApp archive.

Processed raw messages older than `retention.processed_raw_days` are purged after successful real workflow runs. Pending rows are preserved regardless of age.

Run history stores metadata, not the rendered digest or full raw source.

Dry-run artifacts are local owner-only files under the database data directory.

---

## Functional limits of v2

Intentionally out of scope:

- WhatsApp historical API/backfill;
- media OCR/transcription/extraction;
- WhatsApp outbound replies;
- multiple workflows for one group;
- automatic model fallback;
- cross-day semantic unresolved-question state;
- permanent raw-message archive;
- multi-channel delivery beyond email.

Phase 1 handles text and media captions. Media-only placeholders are ignored.

---

## Tests

Primary v2 suite:

```bash
pytest -q tests/test_v2_*.py
```

GitHub Actions runs the same v2 suite on pushes/pull requests that change v2 code/config/package files.

Useful pre-merge checks:

```bash
pytest -q tests/test_v2_*.py
git diff --check
git status --short
```

---

## Shutdown / rollback

Remove scheduled digest timers:

```bash
digest --config ./config.yaml schedule remove
```

Remove the dedicated v2 WhatsApp bridge and collector:

```bash
digest --config ./config.yaml transport remove
```

These commands do not delete SQLite data or `config.yaml`.

If intentionally returning WhatsApp ownership to the normal Hermes gateway, only do so **after** the v2 transport is stopped; then re-enable `WHATSAPP_ENABLED=true` and restart the Hermes gateway. Never run both WhatsApp consumers simultaneously.

---

## What “done” means for this project

The v2 implementation is complete when all of the following are true:

- v2 tests are green;
- transport status is healthy;
- scheduler status is healthy;
- Hermes gateway WhatsApp is disabled;
- user lingering is enabled;
- each production workflow is explicitly initialized;
- real group messages increase pending counts;
- a real scheduled/manual digest reaches SMTP and advances the checkpoint only on success.

After that, the remaining activity is a **multi-day quality pilot**: review actual summaries versus the raw attachment for omissions, hallucinations, grouping, attribution, action capture, unanswered questions, and noise. Quality findings should normally be addressed with workflow instructions or prompt tuning before adding new architecture.
