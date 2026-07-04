-- Memory Ranking — importance, memory type, and access tracking.
-- Idempotent (ADD COLUMN IF NOT EXISTS). Applied by the abi-api migration
-- runner at startup (abi/api/deps.py). abi_memories is owned by abi_agent
-- after abi-setup, so ADD COLUMN succeeds without an owner change (Rule 46
-- only applies to newly-created standalone objects).

BEGIN;

-- Importance in [0,1] (default 0.5 = neutral). Populated by auto-extraction
-- (PR 3) or explicit writes; used as a recall scoring weight.
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS importance REAL DEFAULT 0.5;

-- Coarse memory category (preference | decision | fact | event | identity | other).
-- Nullable; legacy rows stay NULL.
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS memory_type TEXT;

-- Access tracking for future relevance/decay signals.
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS last_accessed TIMESTAMPTZ;
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS access_count INTEGER DEFAULT 0;

COMMIT;
