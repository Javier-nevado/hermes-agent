-- ABI Agents registry
-- Each agent is a Linux user running a Hermes process

CREATE TABLE IF NOT EXISTS abi_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    clearance TEXT NOT NULL DEFAULT 'external'
        CHECK (clearance IN ('admin', 'internal', 'external')),
    telegram_bot TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_abi_agents_username ON abi_agents(username);
