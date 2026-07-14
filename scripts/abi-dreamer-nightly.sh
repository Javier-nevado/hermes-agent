#!/bin/bash
# abi-dreamer-nightly.sh — the nightly Dreamer run, called by the
# abi-dreamer-nightly.timer (system service). Three halves:
#
#   1. dedup (heuristic, in the abi-memory-api container, all agents) —
#      soft-supersedes high-cosine near-duplicates (reversible, audited in
#      dedup_log). Cheap, no LLM. Opt-in (ABI_DREAMER_DEDUP=1).
#   2. extract-llm (in the abi-memory-api container, all agents) — re-extracts
#      entities from thin-coverage memories via the LLM (ADDs to the graph,
#      never rebuilds). Opt-in (ABI_EXTRACT_LLM_ENABLE=1 + GLM_API_KEY).
#      Decrypts in-container; sends plaintext to the configured LLM (egress).
#   3. densify (per-agent on the host, each agent's own LLM) — rewrites verbose
#      memories dense in place (encrypted + re-embedded). See abi-dreamer-densify-all.
#
# Default nightly = DENSIFY ONLY. Dedup + extract-llm are opt-in.
#
# Dedup is SAFE (3.6.0): soft-delete (superseded_by + valid_to) + dedup_log
# audit trail, threshold 0.97 (calibrated 2026-07-14 against decrypted pairs;
# >0.97 + telemetry-skip = ~0% FP). Reversible. The old hard-DELETE >0.90 path
# is gone. Enable with ABI_DREAMER_DEDUP=1 (sets ABI_DEDUP_ENABLE=1 in the
# container; threshold via ABI_DEDUP_THRESHOLD, default 0.97).
#
# extract-llm: NEW LLM-egress flow. Sends decrypted memory content to the LLM
# (default glm-4.7-flash via the ZAI coding subscription). Internal dogfood
# only (.19) until per-box customer egress sign-off. Trial boxes without a DEK
# skip automatically. Off-peak (timer 03:17) — avoids ZAI peak-hour overload
# (08:00-12:00 CEST). Enable with ABI_EXTRACT_LLM_ENABLE=1 + GLM_API_KEY.
#
# A flock guard prevents a manual run and the timer from colliding (the dreamer
# is not safe to run twice concurrently against the same DB).
#
# Per-box config: set ABI_DREAMER_DEDUP / ABI_EXTRACT_LLM_ENABLE / GLM_API_KEY /
# ABI_DEDUP_THRESHOLD in the systemd service file's [Service] Environment=
# (or EnvironmentFile=) before enabling.
set -uo pipefail

LOCK=/run/abi-dreamer.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[abi-dreamer] another run holds $LOCK; exiting."
  exit 0
fi

echo "[abi-dreamer] $(date -u +%FT%TZ) nightly start (densify; dedup=${ABI_DREAMER_DEDUP:-0}; extract-llm=${ABI_EXTRACT_LLM_ENABLE:-0})"

# 1. dedup — container, all agents. Opt-in (ABI_DREAMER_DEDUP=1). Safe soft-delete
#    + audit (3.6.0); threshold 0.97 calibrated. Sets ABI_DEDUP_ENABLE=1 +
#    ABI_DEDUP_THRESHOLD inside the container so phase_dedup actually writes.
if [ "${ABI_DREAMER_DEDUP:-0}" = "1" ]; then
  echo "[abi-dreamer] dedup (in-container, all agents, --write, threshold=${ABI_DEDUP_THRESHOLD:-0.97})..."
  docker exec \
    -e ABI_DEDUP_ENABLE=1 \
    -e ABI_DEDUP_THRESHOLD="${ABI_DEDUP_THRESHOLD:-0.97}" \
    abi-memory-api python3 -m abi.dreamer --phase dedup --write
else
  echo "[abi-dreamer] dedup SKIPPED (set ABI_DREAMER_DEDUP=1)"
fi

# 2. extract-llm — container, all agents. Opt-in (ABI_EXTRACT_LLM_ENABLE=1 +
#    GLM_API_KEY). Re-extracts entities from thin-coverage memories via the LLM
#    (glm-4.7-flash on the ZAI coding subscription). ADDs to the graph, never
#    rebuilds. Decrypts in-container; plaintext egresses to the LLM — internal
#    dogfood only until per-box customer egress sign-off. Trial boxes (no DEK)
#    skip automatically.
if [ "${ABI_EXTRACT_LLM_ENABLE:-0}" = "1" ] && [ -n "${GLM_API_KEY:-}" ]; then
  echo "[abi-dreamer] extract-llm (in-container, all agents, glm-4.7-flash, --write)..."
  docker exec \
    -e GLM_API_KEY="$GLM_API_KEY" \
    abi-memory-api python3 -m abi.dreamer --phase extract-llm \
      --llm-base-url https://api.z.ai/api/coding/paas/v4 \
      --llm-model glm-4.7-flash \
      --llm-api-key-env GLM_API_KEY --write
else
  echo "[abi-dreamer] extract-llm SKIPPED (set ABI_EXTRACT_LLM_ENABLE=1 + GLM_API_KEY)"
fi

# 3. densify — host, per active agent (own LLM each). Safe: in-place rewrite,
#    preserves metadata.original_content audit trail, idempotent.
/usr/local/sbin/abi-dreamer-densify-all

echo "[abi-dreamer] $(date -u +%FT%TZ) nightly done"
