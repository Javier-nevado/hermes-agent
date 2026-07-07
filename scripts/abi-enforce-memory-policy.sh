#!/bin/bash
# abi-enforce-memory-policy.sh — enforce the ABI memory policy on agent homes.
#
# Policy: agents MUST use the abi_memory tools (abi_remember / abi_recall /
# abi_forget, backed by PostgreSQL + pgvector). The built-in local `memory`
# tool (file-based MEMORY.md, ~2200-char cap) is DISABLED.
#
# This script applies 3 layers, idempotently, per agent home:
#   1. config.yaml: memory.memory_enabled=false, memory.user_profile_enabled=false
#      -> agent_init.py never loads MemoryStore, so the local `memory` tool is
#         not registered. The abi_memory PROVIDER (unlimited, encrypted) stays on.
#   2. SOUL.service.md: a root-owned (chown root:root, chmod 644) platform-policy
#      fragment carrying the "MEMORY STORAGE POLICY — MANDATORY" section. The agent
#      (no sudo) CANNOT modify it. hermes load_soul_md() injects it ALONGSIDE the
#      agent's own SOUL.md, so the policy text is tamper-proof.
#   3. SOUL.md: kept AGENT-OWNED (editable) so the agent can evolve its own
#      identity, role, and personal learnings. Any legacy policy section that an
#      older rollout appended here is removed (migrated to SOUL.service.md).
#
# Design: the service owns the platform policy (root-owned SOUL.service.md) AND
# the capability gate (compiled default + file_safety + update re-stamp); the
# agent owns its identity (SOUL.md). The real enforcement that the local memory
# tool cannot be used is the config gate in agent_init.py — the SOUL fragment is
# the behavioral statement of it.
#
# config.yaml is KEPT agent-owned: hermes writes it at runtime (onboarding flags,
# image_gen model, /model). file_safety.py already blocks the agent's write_file
# tool from editing config.yaml. The durable backstop against an update resetting
# config.yaml to defaults is the flipped default in hermes_cli/config.py
# (memory_enabled/user_profile_enabled default False) — which ships in the same
# tarball. This script re-stamps the explicit flags on every install/update.
#
# Ships in the ABI release tarball. Called by:
#   - abi-bootstrap.sh   (fresh install, after provisioning)
#   - abi-update --apply (every in-place update)
#   - operators manually (sudo abi-enforce-memory-policy.sh [agents...])
#
# Usage:
#   sudo abi-enforce-memory-policy.sh              # all /home/*/.hermes
#   sudo abi-enforce-memory-policy.sh sol vera     # specific agents only
#   sudo abi-enforce-memory-policy.sh --check      # report-only, no changes
#
# Exit 0 always (best-effort across agents; per-agent failures are reported).

TS="$(date +%F-%H%M%S 2>/dev/null || echo stamp)"
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; shift; fi

log() { echo "[mempol] $*"; }

# --- pick a python3 with a YAML lib (ruamel = comment-preserving; PyYAML fallback).
# Checks the agent's own venv (per-user ~/.hermes/hermes-agent/.venv) first, then
# the shared bare-metal venv (/opt/hermes-agent), then system python3.
detect_python() {
  local home="$1" c
  for c in "$home/hermes-agent/.venv/bin/python3" /opt/hermes-agent/.venv/bin/python3 /opt/hermes-gateway/.venv/bin/python3 python3; do
    command -v "$c" >/dev/null 2>&1 && "$c" -c 'import ruamel.yaml' 2>/dev/null && { echo "$c"; return; }
  done
  for c in "$home/hermes-agent/.venv/bin/python3" /opt/hermes-agent/.venv/bin/python3 python3; do
    command -v "$c" >/dev/null 2>&1 && "$c" -c 'import yaml' 2>/dev/null && { echo "$c"; return; }
  done
}

edit_config() {
  # $1 = hermes home. Sets memory.*_enabled=false. Comment-preserving if ruamel.
  local PY
  PY="$(detect_python "$1")"
  [ -n "$PY" ] || { log "no python3 with YAML lib found"; return 1; }
  "$PY" - "$1" <<'PYEOF'
import sys
path = sys.argv[1] + "/config.yaml"
try:
    from ruamel.yaml import YAML
    y = YAML(); y.preserve_quotes = True; y.indent(mapping=2, sequence=4, offset=2)
    with open(path) as f: data = y.load(f)
    mem = data.setdefault("memory", {})
    mem["memory_enabled"] = False
    mem["user_profile_enabled"] = False
    with open(path, "w") as f: y.dump(data, f)
except ImportError:
    import yaml
    with open(path) as f: data = yaml.safe_load(f) or {}
    mem = data.setdefault("memory", {})
    mem["memory_enabled"] = False
    mem["user_profile_enabled"] = False
    with open(path, "w") as f: yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
PYEOF
}

SOUL_POLICY='## MEMORY STORAGE POLICY — MANDATORY

**SOUL.service.md — Opteia ABI platform policy. Root-owned; the agent cannot edit
this file. It is injected alongside the editable SOUL.md.**

**ALWAYS use the abi_memory tools. NEVER use the local `memory` tool.**

The local `memory` tool is **disabled** in config. The only memory tools available are the abi_memory ones. If you ever see a `memory` tool in your tool list, ignore it — it should not be there.

| Operation | Use this tool | Forbidden |
|-----------|---------------|-----------|
| Store any fact, decision, observation, or context | `abi_remember` | local `memory` / file writes |
| Recall past context, search history, find facts | `abi_recall` | local `memory` / file reads |
| Remove outdated information | `abi_forget` | manual file edits |

### Why

- `abi_remember` stores to PostgreSQL + pgvector — **unlimited capacity**, semantic search, fast recall.
- The local `memory` tool wrote to MEMORY.md — bounded to ~2200 chars, fills up fast, no semantic search. **Now disabled.**
- Everything you learn — tasks, decisions, outcomes, operational gotchas — MUST go to `abi_remember`.
- Before answering questions about past work, preferences, or context — call `abi_recall` first.

### Rules

1. **At session start:** relevant memory is auto-loaded by the abi_memory provider; call `abi_recall` for specific past context when needed.
2. **When you learn something new** (a fact, a pattern, a decision): immediately call `abi_remember` with a concise summary.
3. **Before answering "How do I..." or "What did we..." questions:** call `abi_recall` first — do not guess from this session alone.
4. **Never write to local files for memory** — only `abi_remember`. Files are for deliverables (reports, scripts, configs), not for memory.
5. **DLP levels:** `confidential` for user-specific sensitive info, `internal` for general Opteia context (default), `public` for non-sensitive facts.
6. **When in doubt, store it.** Memory is cheap; forgetting is expensive. A fact stored can be recalled; a fact not stored is gone forever.
'

enforce_one() {
  local user="$1"
  local home="/home/$user/.hermes"   # separate stmt: bash expands $user BEFORE
                                     # assigning it in a single `local a=X b=$a`
  [ -d "$home" ] || { log "$user: no ~/.hermes — skip"; return; }
  [ -f "$home/config.yaml" ] || { log "$user: no config.yaml — skip"; return; }

  # Skip the operator's login home + root. The policy targets managed ABI
  # agents, not the operator's CLI home. $SUDO_USER = whoever invoked sudo.
  if [ "$user" = "root" ] || { [ -n "${SUDO_USER:-}" ] && [ "$user" = "$SUDO_USER" ]; }; then
    log "$user: operator/root login home — skip"; return
  fi

  # Only enforce on ABI agent homes (memory provider = abi_memory[_api]).
  grep -q "abi_memory" "$home/config.yaml" 2>/dev/null || { log "$user: not an ABI agent (no abi_memory provider) — skip"; return; }

  # --- report current state ---
  local cur_me cur_up svc_present svc_owner soul_owner legacy_in_soul
  cur_me="$(grep -E '^\s*memory_enabled:' "$home/config.yaml" 2>/dev/null | tail -1 | tr -d ' ' || true)"
  cur_up="$(grep -E '^\s*user_profile_enabled:' "$home/config.yaml" 2>/dev/null | tail -1 | tr -d ' ' || true)"
  svc_present=0; [ -f "$home/SOUL.service.md" ] && svc_present=1
  svc_owner="$(stat -c '%U' "$home/SOUL.service.md" 2>/dev/null || echo '-')"
  soul_owner="$(stat -c '%U' "$home/SOUL.md" 2>/dev/null || echo '-')"
  legacy_in_soul=0; [ -f "$home/SOUL.md" ] && grep -q "^## MEMORY STORAGE POLICY" "$home/SOUL.md" 2>/dev/null && legacy_in_soul=1

  if [ "$CHECK_ONLY" -eq 1 ]; then
    log "$user: memory_enabled=${cur_me:-<absent>} user_profile_enabled=${cur_up:-<absent>} | SOUL.service.md=$svc_present($svc_owner) SOUL.md_owner=$soul_owner legacy_policy_in_SOUL=$legacy_in_soul"
    return
  fi

  # --- backups (no-clobber: keep the first backup of this run) ---
  cp -n "$home/config.yaml" "$home/config.yaml.bak-mempol-$TS" 2>/dev/null || true
  [ -f "$home/SOUL.md" ] && cp -n "$home/SOUL.md" "$home/SOUL.md.bak-mempol-$TS" 2>/dev/null || true

  # --- layer 1: config flags (keep config.yaml agent-owned; hermes writes it) ---
  local owner
  owner="$(stat -c '%U:%G' "$home/config.yaml" 2>/dev/null || echo "$user:$user")"
  if edit_config "$home"; then
    chown "$owner" "$home/config.yaml" 2>/dev/null || true
    log "$user: config flags -> false (owner kept $owner)"
  else
    log "$user: WARN config edit failed — flags unchanged"
  fi

  # --- layers 2 + 3: SOUL.service.md (root-owned platform policy) + clean SOUL.md (agent-owned) ---
  # Write the policy to SOUL.service.md (root-owned, tamper-proof). The agent
  # (no sudo) cannot edit it; hermes load_soul_md() injects it alongside the
  # agent's editable SOUL.md. SOUL.md stays agent-owned (editable identity).
  printf '%s\n' "$SOUL_POLICY" > "$home/SOUL.service.md"
  chown root:root "$home/SOUL.service.md" 2>/dev/null && chmod 644 "$home/SOUL.service.md"
  log "$user: SOUL.service.md written (root-owned)"

  # Version-aware migration of older rollouts (which appended the policy to
  # SOUL.md AND root-owned the whole file). We un-root SOUL.md unconditionally
  # (agent must be able to edit its own identity). But we only REMOVE the legacy
  # policy section from SOUL.md if the deployed hermes code already reads
  # SOUL.service.md — otherwise the running (old) agent would lose the policy
  # text from its prompt until the code update lands. Once new code is present,
  # a re-run cleans SOUL.md (policy now lives in SOUL.service.md).
  local code_reads_service=0 pb
  for pb in /opt/hermes-agent/agent/prompt_builder.py "$home/hermes-agent/agent/prompt_builder.py"; do
    [ -f "$pb" ] && grep -q "SOUL.service.md" "$pb" 2>/dev/null && { code_reads_service=1; break; }
  done
  if [ "$code_reads_service" -eq 1 ] && [ -f "$home/SOUL.md" ] && grep -q "^## MEMORY STORAGE POLICY" "$home/SOUL.md" 2>/dev/null; then
    sed -i '/^## MEMORY STORAGE POLICY/,$d' "$home/SOUL.md"
    log "$user: removed legacy policy from SOUL.md (deployed code reads SOUL.service.md)"
  fi
  if [ -f "$home/SOUL.md" ]; then
    chown "$user:$user" "$home/SOUL.md" 2>/dev/null || true
    chmod 644 "$home/SOUL.md" 2>/dev/null || true
    log "$user: SOUL.md agent-owned (editable)"
  else
    log "$user: no SOUL.md — identity file skipped"
  fi
}

# --- discover agents (explicit args, else every /home/*/.hermes) ---
if [ "$#" -gt 0 ]; then
  AGENTS="$*"
else
  AGENTS=""
  for d in /home/*/.hermes; do [ -d "$d" ] && AGENTS="$AGENTS $(basename "$(dirname "$d")")"; done
fi
AGENTS="$(echo $AGENTS | xargs)"  # trim

if [ -z "$AGENTS" ]; then log "no agent homes found under /home/*/.hermes"; exit 0; fi

log "target agents: $AGENTS"
[ "$CHECK_ONLY" -eq 1 ] && log "CHECK-ONLY mode (no changes)"
for a in $AGENTS; do enforce_one "$a"; done
log "done."
