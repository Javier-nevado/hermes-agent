-- Migration 001: base abi_memories table.
--
-- Promoted from the inline CREATE in scripts/abi-setup (which abi-bootstrap.sh never
-- runs). Without this, fresh DBs have no abi_memories table, so 002_memory_intelligence
-- ALTERs a non-existent table and 003/004/005 cascade-fail — leaving a fresh install
-- with no memory schema. Running this through the file-migration runner (deps.py)
-- means every DB, fresh or existing, gets the base table idempotently on startup.
-- CREATE IF NOT EXISTS makes this a safe no-op on boxes where abi-setup already
-- created it.

BEGIN;

CREATE TABLE IF NOT EXISTS abi_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    embedding vector(384),
    dlp_level TEXT NOT NULL DEFAULT 'internal'
        CHECK (dlp_level IN ('public', 'internal', 'confidential')),
    agent_name TEXT NOT NULL,
    user_id TEXT,
    source_type TEXT DEFAULT 'agent_tool',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    fts tsvector
);

CREATE INDEX IF NOT EXISTS idx_abi_memories_embedding
    ON abi_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_abi_memories_fts ON abi_memories USING GIN(fts);

COMMIT;
