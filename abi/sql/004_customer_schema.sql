-- Phase 5: Custom Tables API — customer schema isolation
-- Creates the `customer` schema for agent-driven business tables.
-- All custom tables live here, isolated from core `public` schema.
--
-- The grantee role is parameterized: apply with
--   psql -v db_user=<role> -f 004_customer_schema.sql
-- Defaults to `abi_agent` when db_user is unset.

\if :{?db_user}
\else
  \set db_user abi_agent
\endif

BEGIN;

CREATE SCHEMA IF NOT EXISTS customer;

-- Grant full privileges on customer schema to the DB role
GRANT ALL ON SCHEMA customer TO :"db_user";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA customer TO :"db_user";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA customer TO :"db_user";

-- Ensure future tables created by any role are accessible to the DB role
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT ALL ON TABLES TO :"db_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA customer GRANT ALL ON SEQUENCES TO :"db_user";

COMMIT;
