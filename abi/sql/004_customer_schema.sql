-- Phase 5: Custom Tables API — customer schema isolation
-- Creates the `customer` schema for agent-driven business tables.
-- All custom tables live here, isolated from core `public` schema.

BEGIN;

CREATE SCHEMA IF NOT EXISTS customer;

-- Grant full privileges on customer schema to abi_agent
GRANT ALL ON SCHEMA customer TO abi_agent;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA customer TO abi_agent;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA customer TO abi_agent;

-- Ensure future tables created by any role are accessible to abi_agent
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT ALL ON TABLES TO abi_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT ALL ON SEQUENCES TO abi_agent;

COMMIT;
