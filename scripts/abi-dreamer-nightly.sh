#!/bin/bash
# abi-dreamer-nightly.sh — the nightly Dreamer run, called by the
# abi-dreamer-nightly.timer (system service). Two halves:
#
#   1. dedup (heuristic, in the abi-memory-api container, all agents) — removes
#      >0.90-cosine near-duplicates. Cheap, no LLM.
#   2. densify (per-agent on the host, each agent's own LLM) — rewrites verbose
#      memories dense in place (encrypted + re-embedded). See abi-dreamer-densify-all.
#
# Default nightly = dedup + densify. The other heuristic phases (contradictions,
# consolidation, temporal) are opt-in (run manually with --phase heuristic) —
# they emit low-signal string-template insights; enable them only if wanted.
#
# A flock guard prevents a manual run and the timer from colliding (the dreamer
# is not safe to run twice concurrently against the same DB).
#
# Per-box config: set ABI_DENSIFY_AGENTS (etc.) in the systemd service file's
# [Service] Environment= before enabling (see abi-dreamer-nightly.service).
set -uo pipefail

LOCK=/run/abi-dreamer.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[abi-dreamer] another run holds $LOCK; exiting."
  exit 0
fi

echo "[abi-dreamer] $(date -u +%FT%TZ) nightly start (dedup + densify)"

# 1. dedup — container, all agents.
docker exec abi-memory-api python3 -m abi.dreamer --phase dedup --write

# 2. densify — host, per active agent (own LLM each).
/usr/local/sbin/abi-dreamer-densify-all

echo "[abi-dreamer] $(date -u +%FT%TZ) nightly done"
