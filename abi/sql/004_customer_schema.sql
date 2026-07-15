-- Phase 5: Custom Tables API — customer schema isolation
-- Creates the `customer` schema for agent-driven business tables.
-- All custom tables live here, isolated from core `public` schema.

BEGIN;

CREATE SCHEMA IF NOT EXISTS customer;

-- Grant full privileges on customer schema to the connecting (app) DB role.
-- CURRENT_USER is role-agnostic: resolves to abi_agent on standard boxes and to
-- opteia on legacy J2 boxes, so this migration applies cleanly fleet-wide.
-- (Previously hardcoded TO abi_agent, which fails where that role doesn't exist
-- and cascaded 005/006 into "current transaction is aborted".)
GRANT ALL ON SCHEMA customer TO CURRENT_USER;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA customer TO CURRENT_USER;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA customer TO CURRENT_USER;

-- Ensure future tables created by any role are accessible to that same role.
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT ALL ON TABLES TO CURRENT_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT ALL ON SEQUENCES TO CURRENT_USER;

COMMIT;
