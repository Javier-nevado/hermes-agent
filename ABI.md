# ABI — Artificial Business Intelligence Platform

> Multi-agent platform built on Hermes Agent (MIT license, v0.14.0)

## Architecture

**N independent Hermes processes**, each running as a separate Linux user with its own Telegram bot. No centralized router — the bot IS the router.

## Fork Strategy

- **Upstream:** NousResearch/hermes-agent (remote: upstream)
- **Fork:** Javier-nevado/hermes-agent (remote: origin)
- **Branch:** abi/main from release tags (not rolling)
- **All ABI code:** abi/ namespace (zero merge conflicts)
- **Upstream patches:** fewer than 5 files, marked with ABI-PATCH / END ABI-PATCH
- **Merge schedule:** Quarterly, by tag only

## Hermes Internals (Key for ABI Development)

### MemoryProvider Interface (agent/memory_provider.py)

Core methods: name, is_available(), initialize(), system_prompt_block(), prefetch(), queue_prefetch(), sync_turn(), get_tool_schemas(), handle_tool_call(), shutdown()

Optional hooks: on_turn_start, on_session_end, on_session_switch, on_pre_compress, on_memory_write, on_delegation

### initialize() kwargs (from run_agent.py ~line 2035)

Always: session_id, platform, hermes_home, agent_context, agent_identity, agent_workspace

Gateway: user_id, user_name, chat_id, chat_name, chat_type, thread_id, gateway_session_key

### Plugin Registration (plugins/memory/__init__.py)

Patterns: register(ctx) or auto-discovery of MemoryProvider subclass
Locations: bundled plugins/memory/name/ or user $HERMES_HOME/plugins/name/

### Session Context (gateway/session_context.py)

HERMES_SESSION_USER_ID ContextVar, get_session_env()

### Profile System

Own HERMES_HOME, config.yaml, .env, SOUL.md, skills/ per profile

## Upstream Files Patched (target <5)

| File | Patch |
|------|-------|
| run_agent.py | Pass agent_name/clearance to memory provider |
| tools/file_tools.py | Conditional import of ABI sandbox resolver |
| agent/file_safety.py | Extend deny list for ABI sandbox paths |
| gateway/platforms/telegram.py | Inject user identity into session context |

## Phased Implementation

| Phase | Name | Days | Status |
|-------|------|------|--------|
| 0 | Foundation | 2 | In Progress |
| 1 | PostgreSQL Memory | 5 | Pending |
| 2 | File Namespacing | 3 | Pending |
| 3 | Tool Pool | 2 | Pending |
| 4 | Agent Provisioning | 3 | Pending |
| 5 | First Deploy | 2 | Pending |
| 6 | Multi-Agent | 3 | Pending |
| 7 | Hardening | 4 | Pending |

## Development Rules

1. All new code in abi/ — zero conflicts with upstream
2. Every upstream patch wrapped in ABI-PATCH / END ABI-PATCH
3. Prefer subclass/plugin over patching upstream
4. Every patch references a PRD/phase and includes a test
5. No silent fallbacks — errors must be structured and visible
6. Strong typing on all interfaces and DTOs
7. Structured logging only
