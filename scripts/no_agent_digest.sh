#!/usr/bin/env bash
# Inactive design artifact. G2 may copy this exact file to
# $HERMES_HOME/scripts/whatsapp-tech-digest-no-agent.sh only after approval.
set -euo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
POLICY="$PROJECT_DIR/config/digest.policy.json"

# The installed Hermes no_agent runner delivers nonempty stdout verbatim. The
# application owns fixed-recipient SMTP; successful and empty runs are silent.
# This inactive wrapper has no agent prompt, no recipient argument, and emits a
# nonzero status on delivery/model/lock errors. Activation remains DST-blocked.
exec /usr/bin/env python3 -m whatsapp_tech_digest.run \
  --policy "$POLICY" \
  --spool "$PROJECT_DIR/data/spool.sqlite3" \
  --lock "$PROJECT_DIR/data/digest.lock"
