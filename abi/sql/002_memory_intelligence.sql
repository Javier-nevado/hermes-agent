-- Phase 8: Memory Intelligence — schema migration
-- Adds: entity extraction, BM25 tsvector, temporal facts

BEGIN;

-- Entity table: extracted named entities (people, companies, etc.)
CREATE TABLE IF NOT EXISTS abi_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('person','company','role','project','product','amount','date','location','technology','other')),
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, type)
);

-- Edge table: typed relationships between entities
CREATE TABLE IF NOT EXISTS abi_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES abi_entities(id) ON DELETE CASCADE,
    target_id UUID REFERENCES abi_entities(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    memory_id UUID REFERENCES abi_memories(id) ON DELETE CASCADE,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, target_id, relation, memory_id)
);

-- Many-to-many link between memories and entities
CREATE TABLE IF NOT EXISTS abi_memory_entities (
    memory_id UUID REFERENCES abi_memories(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES abi_entities(id) ON DELETE CASCADE,
    PRIMARY KEY(memory_id, entity_id)
);

-- BM25: tsvector column for full-text search
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS fts tsvector;

-- GIN index for fast tsvector queries
CREATE INDEX IF NOT EXISTS idx_abi_memories_fts ON abi_memories USING GIN(fts);

-- Auto-update trigger: rebuilds tsvector on INSERT/UPDATE of content
CREATE OR REPLACE FUNCTION abi_memories_fts_update() RETURNS trigger AS $$
BEGIN
    NEW.fts := to_tsvector('english', COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_abi_memories_fts ON abi_memories;
CREATE TRIGGER trg_abi_memories_fts
    BEFORE INSERT OR UPDATE OF content ON abi_memories
    FOR EACH ROW EXECUTE FUNCTION abi_memories_fts_update();

-- Temporal fields (ABI3-39)
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ;
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ;
ALTER TABLE abi_memories ADD COLUMN IF NOT EXISTS superseded_by UUID REFERENCES abi_memories(id);

-- Indexes for entity/edge lookups
CREATE INDEX IF NOT EXISTS idx_entities_name ON abi_entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON abi_entities(type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON abi_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON abi_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON abi_edges(relation);
CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON abi_memory_entities(entity_id);

COMMIT;
