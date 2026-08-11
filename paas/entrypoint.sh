#!/usr/bin/env bash
# abi-paas entrypoint — supervisord as PID 1 (under tini).
# supervisord starts nginx + the mgmt API + (as they're deployed) the apps.
# ABI_PAAS_TOKEN is the shared bearer the agent + mgmt API use to authenticate deploys.
set -euo pipefail

: "${ABI_PAAS_TOKEN:?ABI_PAAS_TOKEN is required — set it in the box .env (see provision-paas.sh)}"

# supervisord reads its config + the include dir, then stays foreground (-n) under tini.
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
