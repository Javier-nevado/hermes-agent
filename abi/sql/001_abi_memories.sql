-- Migration 001: abi_memories — core memory store (PostgreSQL + pgvector)
-- 384-dim vector embeddings (All-MiniLM-L6-v2) + tsvector full-text search.
-- HNSW index for fast approximate nearest-neighbour cosine search.
-- Previously inlined in scripts/abi-setup; extracted so the migration set is
-- self-contained and applied in sorted order by the setup script.
-- (The pgvector extension itself is created by the setup script / operator.)

CREATE TABLE IF NOT EXISTS abi_memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content     TEXT NOT NULL,
    embedding   vector(384),
    dlp_level   TEXT NOT NULL DEFAULT 'internal'
                    CHECK (dlp_level IN ('public', 'internal', 'confidential')),
    agent_name  TEXT NOT NULL,
    user_id     TEXT,
    tenant_id   UUID,
    source_type TEXT DEFAULT 'agent_tool',
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    fts         tsvector
);

-- HNSW (not ivfflat): better recall without tuning `lists`, matches deployed instances.
CREATE INDEX IF NOT EXISTS idx_abi_memories_embedding
    ON abi_memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_abi_memories_fts
    ON abi_memories USING GIN (fts);
