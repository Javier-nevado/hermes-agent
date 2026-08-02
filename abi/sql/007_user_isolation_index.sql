-- Migration 007: index for per-user read isolation.
--
-- Recall now narrows by (agent_name, user_id) in private (1:1 DM) scopes so one
-- user cannot surface another user's memories on a shared agent. This composite
-- index keeps that filter fast as abi_memories grows. Idempotent (IF NOT EXISTS);
-- no behaviour change for ownerless/system rows (user_id NULL stays visible).
-- See plan: per-user-read-isolation-for-abi-memory.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_abi_memories_agent_user
    ON abi_memories(agent_name, user_id);

COMMIT;
