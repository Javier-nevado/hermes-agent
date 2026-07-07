#!/bin/bash
# abi-dreamer-nightly.sh — the nightly Dreamer run, called by the
# abi-dreamer-nightly.timer (system service). Two halves:
#
#   1. dedup (heuristic, in the abi-memory-api container, all agents) — removes
#      high-cosine near-duplicates. Cheap, no LLM. OFF BY DEFAULT (see below).
#   2. densify (per-agent on the host, each agent's own LLM) — rewrites verbose
#      memories dense in place (encrypted + re-embedded). See abi-dreamer-densify-all.
#
# Default nightly = DENSIFY ONLY. Dedup is opt-in (ABI_DREAMER_DEDUP=1).
#
# WHY DEDUP IS OFF: phase_dedup hard-deletes the older of any pair with cosine
# similarity >0.90, with NO audit trail. On All-MiniLM-L6-v2 that threshold is
# too low for short factual memories — they cluster tightly. Measured on .19
# (2026-07-07): atlas alone had 10,646 pairs >0.90 (1,944 at 0.90-0.91); most
# are distinct-but-RELATED facts, not true duplicates. Enabling as-is would
# silently delete real memories. Calibrate the threshold against DECRYPTED pairs
# (and raise the >0.90 constant in phase_dedup, or make it env-configurable)
# before turning ABI_DREAMER_DEDUP on.
#
# The other heuristic phases (contradictions, consolidation, temporal) are also
# opt-in (run manually with --phase heuristic) — they emit low-signal
# string-template insights; enable only if wanted.
#
# A flock guard prevents a manual run and the timer from colliding (the dreamer
# is not safe to run twice concurrently against the same DB).
#
# Per-box config: set ABI_DENSIFY_AGENTS / ABI_DREAMER_DEDUP (etc.) in the
# systemd service file's [Service] Environment= before enabling.
set -uo pipefail

LOCK=/run/abi-dreamer.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[abi-dreamer] another run holds $LOCK; exiting."
  exit 0
fi

echo "[abi-dreamer] $(date -u +%FT%TZ) nightly start (densify; dedup=${ABI_DREAMER_DEDUP:-0})"

# 1. dedup — container, all agents. OFF by default (threshold uncalibrated; see
#    header). Enable with ABI_DREAMER_DEDUP=1 only after calibrating phase_dedup's
#    >0.90 constant against decrypted pairs.
if [ "${ABI_DREAMER_DEDUP:-0}" = "1" ]; then
  echo "[abi-dreamer] dedup (in-container, all agents, --write)..."
  docker exec abi-memory-api python3 -m abi.dreamer --phase dedup --write
else
  echo "[abi-dreamer] dedup SKIPPED (off until threshold calibrated; set ABI_DREAMER_DEDUP=1)"
fi

# 2. densify — host, per active agent (own LLM each). Safe: in-place rewrite,
#    preserves metadata.original_content audit trail, idempotent.
/usr/local/sbin/abi-dreamer-densify-all

echo "[abi-dreamer] $(date -u +%FT%TZ) nightly done"
