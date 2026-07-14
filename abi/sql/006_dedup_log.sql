-- Migration 006: dedup audit log.
--
-- Soft-delete dedup (dreamer phase_dedup) supersedes near-duplicate memories
-- (UPDATE abi_memories SET superseded_by=keeper, valid_to=NOW()), replacing the old
-- hard-DELETE (>0.90) phase. This table records every supersede so the action is
-- auditable and reversible: each row names the keeper, the superseded (loser), the
-- cosine similarity, and the agent. Plain UUID columns (no FK) on purpose — the audit
-- must survive if a memory is later hard-deleted via /forget.
-- Threshold rationale + telemetry-skip: outputs/dedup-monitor/calibration-20260714.md
-- Plan: scalable-gathering-rain.md Step 1.

BEGIN;

CREATE TABLE IF NOT EXISTS dedup_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keeper_id       UUID NOT NULL,
    superseded_id   UUID NOT NULL,
    sim             DOUBLE PRECISION NOT NULL,
    agent_name      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dedup_log_agent_created_idx
    ON dedup_log (agent_name, created_at DESC);

COMMIT;
