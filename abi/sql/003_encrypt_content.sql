-- Phase 4: Content encryption — modify FTS trigger for encrypted content
--
-- The API now encrypts content before INSERT and passes fts explicitly
-- (to_tsvector from plaintext). The trigger must not overwrite pre-set fts
-- values, otherwise encrypted content would produce garbage tsvectors.

BEGIN;

CREATE OR REPLACE FUNCTION abi_memories_fts_update() RETURNS trigger AS $$
BEGIN
    IF NEW.fts IS NULL THEN
        NEW.fts := to_tsvector('english', COALESCE(NEW.content, ''));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
