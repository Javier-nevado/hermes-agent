"""ABI Agent Provisioning Orchestrator.

Interactive wizard + CLI-flag mode for agent creation.

Usage (interactive):
    sudo python3 scripts/abi-provision

Usage (CLI flags):
    sudo python3 scripts/abi-provision \\
        --name ailean \\
        --display-name "AIlean" \\
        --role CEO \\
        --clearance admin \\
        --mcps brevo,m365,twenty,proxmox \\
        --telegram-token 123456:ABC

Steps:
    1. Create Linux user
    2. Set home permissions
    3. Create workspace directory structure
    4. Generate age keypair (for credential encryption, Phase 7)
    5. Generate config.yaml
    6. Write .env
    7. Write SOUL.md
    8. Create PostgreSQL role (optional)
    9. Insert agent record in database
   10. Generate and install systemd service
   11. Enable and start service
   12. Verify (health check)
"""

import argparse
import os
import re
import secrets
import string
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Add repo root to path so we can import hermes_cli
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from abi.provision.systemd import generate_service, install_service, start_service, get_service_status
from abi.provision.telegram import configure_agent_bot, verify_bot_token

# Default paths
HERMES_DIR = Path("/opt/hermes-agent")
HERMES_BIN = HERMES_DIR / ".venv/bin/hermes"

# Database defaults
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "abi_memory"
DB_ADMIN_USER = "abi_agent"
DB_ADMIN_PASS = "abi_local_dev_2026"

# Role definitions for Step 2
ROLE_OPTIONS = ["CEO", "CTO", "Marketing", "Sales", "Ops", "Finance"]

# Clearance descriptions for Step 3
CLEARANCE_OPTIONS = [
    ("admin",    "sees own confidential + all internal/public"),
    ("internal", "sees all internal + public"),
    ("external", "sees only public"),
]

# Provider-specific default models
PROVIDER_DEFAULT_MODELS = {
    "zai": "glm-5.1",
    "anthropic": "claude-sonnet-4-6",
    "openrouter": "anthropic/claude-sonnet-4-6",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-2.5-pro",
    "xai": "grok-3",
    "ollama-cloud": "llama3",
}

# Provider-specific env var names (primary API key env var)
PROVIDER_KEY_ENV = {
    "zai": "GLM_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "huggingface": "HF_API_KEY",
    "novita": "NOVITA_API_KEY",
    "arcee": "ARCEE_API_KEY",
    "gmi": "GMI_API_KEY",
}

# Provider-specific base URL defaults
PROVIDER_BASE_URLS = {
    "zai": "https://api.z.ai/api/coding/paas/v4/",
    "anthropic": "",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "",
    "xai": "https://api.x.ai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "novita": "",
    "arcee": "https://api.arcee.ai/api/v1",
    "gmi": "https://api.gmi-serving.com/v1",
}

# Platform display names
PLATFORM_NAMES = {
    "telegram": "Telegram",
    "slack": "Slack",
    "discord": "Discord",
    "whatsapp": "WhatsApp",
    "signal": "Signal",
    "matrix": "Matrix",
    "email": "Email",
    "mattermost": "Mattermost",
    "feishu": "Feishu",
    "dingtalk": "DingTalk",
    "weixin": "WeChat",
    "webhook": "Webhook",
    "homeassistant": "Home Assistant",
    "bluebubbles": "BlueBubbles (iMessage)",
    "msgraph_webhook": "MS Graph Webhook",
    "sms": "SMS",
    "yuanbao": "Yuanbao",
    "qqbot": "QQ Bot",
    "wecom": "WeCom",
    "wecom_callback": "WeCom Callback",
}

# Files to exclude from platform discovery
PLATFORM_EXCLUDE = {
    "__init__", "base", "_http_client_limits", "helpers", "api_server",
    "telegram_network", "signal_rate_limit", "feishu_comment", "feishu_comment_rules",
    "yuanbao_media", "yuanbao_proto", "yuanbao_sticker", "wecom_crypto",
}

SOUL_TEMPLATE = """# {display_name}

You are {display_name}, {role_description}.

## Your Mission
Help the business run smoothly. Be proactive, professional, and concise.

## Personality
- Professional but approachable
- Data-driven and practical
- Proactive — suggest improvements when you see opportunities

## Guidelines
- Always verify before acting on important decisions
- Be transparent about what you know and don't know
- Keep responses concise and actionable
"""


class ProvisioningError(Exception):
    pass


# ─── Interactive helpers ───────────────────────────────────────────────

def prompt(text: str, default: str = None) -> str:
    """Prompt for text input with optional default."""
    if default:
        hint = f" [{default}]"
    else:
        hint = ""
    try:
        val = input(f"  {text}{hint}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(1)
    return val if val else (default or "")


def _try_curses_single_select(title: str, items: List[str], default_index: int = 0) -> int:
    """Try curses TUI, fall back to numbered prompt."""
    try:
        from hermes_cli.curses_ui import curses_single_select, flush_stdin
        idx = curses_single_select(title, items, default_index)
        flush_stdin()
        if idx is None:
            print("  Cancelled.")
            sys.exit(0)
        return idx
    except Exception:
        # Fallback: numbered menu
        return _numbered_select(title, items, default_index)


def _try_curses_checklist(title: str, items: List[str], preselected: Set[int] = None) -> Set[int]:
    """Try curses multi-select, fall back to numbered prompt."""
    try:
        from hermes_cli.curses_ui import curses_checklist, flush_stdin
        selected = curses_checklist(title, items, preselected or set())
        flush_stdin()
        return selected
    except Exception:
        return _numbered_checklist(title, items, preselected or set())


def _numbered_select(title: str, items: List[str], default_index: int = 0) -> int:
    """Text-based numbered menu fallback."""
    print(f"\n  {title}")
    for i, item in enumerate(items):
        marker = ">" if i == default_index else " "
        print(f"  {marker} {i + 1}. {item}")
    try:
        val = input(f"  Choice [{default_index + 1}]: ").strip()
        if not val:
            return default_index
        idx = int(val) - 1
        if 0 <= idx < len(items):
            return idx
        return default_index
    except (ValueError, KeyboardInterrupt, EOFError):
        return default_index


def _numbered_checklist(title: str, items: List[str], preselected: Set[int]) -> Set[int]:
    """Text-based multi-select fallback."""
    print(f"\n  {title}")
    print("  Enter numbers separated by commas.")
    for i, item in enumerate(items):
        mark = "[x]" if i in preselected else "[ ]"
        print(f"  {mark} {i + 1}. {item}")
    try:
        val = input(f"  Select []: ").strip()
        if not val:
            return preselected
        indices = set()
        for part in val.split(","):
            idx = int(part.strip()) - 1
            if 0 <= idx < len(items):
                indices.add(idx)
        return indices
    except (ValueError, KeyboardInterrupt, EOFError):
        return preselected


# ─── Discovery helpers ─────────────────────────────────────────────────

def get_provider_list() -> List[Tuple[str, str, str]]:
    """Get list of AI providers from Hermes CANONICAL_PROVIDERS.

    Returns: [(slug, label, tui_desc), ...]
    """
    try:
        from hermes_cli.models import CANONICAL_PROVIDERS
        return [(p.slug, p.label, p.tui_desc) for p in CANONICAL_PROVIDERS]
    except Exception:
        # Fallback: minimal list
        return [
            ("zai", "Z.AI / GLM", "Z.AI / GLM (Zhipu AI direct API)"),
            ("anthropic", "Anthropic", "Anthropic (Claude)"),
            ("openrouter", "OpenRouter", "OpenRouter (100+ models)"),
            ("deepseek", "DeepSeek", "DeepSeek (direct API)"),
            ("gemini", "Google AI Studio", "Google AI Studio (Gemini)"),
            ("xai", "xAI", "xAI (Grok)"),
            ("custom", "Custom / Local", "Custom or local endpoint"),
        ]


def discover_mcp_servers() -> List[Tuple[str, str]]:
    """Discover available MCP servers from abi/mcps/servers/.

    Returns: [(name, path), ...] sorted alphabetically.
    """
    servers_dir = HERMES_DIR / "abi" / "mcps" / "servers"
    if not servers_dir.exists():
        return []
    result = []
    for d in sorted(servers_dir.iterdir()):
        if d.is_dir() and d.name.startswith("mcp_") and (d / "server.py").exists():
            name = d.name[4:]  # strip mcp_ prefix
            result.append((name, str(d / "server.py")))
    return result


def discover_platforms() -> List[Tuple[str, str]]:
    """Discover communication platforms from gateway/platforms/.

    Returns: [(slug, display_name), ...] sorted alphabetically.
    """
    platforms_dir = HERMES_DIR / "gateway" / "platforms"
    if not platforms_dir.exists():
        return [("telegram", "Telegram")]
    result = []
    for f in sorted(platforms_dir.iterdir()):
        if f.suffix == ".py" and f.stem not in PLATFORM_EXCLUDE:
            name = f.stem
            display = PLATFORM_NAMES.get(name, name.replace("_", " ").title())
            result.append((name, display))
    return result


def read_existing_env(key_env: str) -> Optional[str]:
    """Read an existing env var value from ~/.hermes/.env."""
    default_env = Path.home() / ".hermes" / ".env"
    if default_env.exists():
        for line in default_env.read_text().splitlines():
            if line.startswith(f"{key_env}="):
                return line.split("=", 1)[1].strip()
    return None


# ─── Interactive wizard ────────────────────────────────────────────────

def interactive_main() -> None:
    """Run the 7-step interactive provisioning wizard."""

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║     ABI Agent Provisioning Wizard    ║")
    print("  ╚══════════════════════════════════════╝")
    print()

    # ── Step 1: Name ────────────────────────────────────────────────
    print("  ── Step 1/7: Name Your Agent ──")
    name = prompt("Agent name (lowercase, no spaces)")
    while not name or not re.match(r'^[a-z][a-z0-9_]*$', name):
        print("    Must start with lowercase letter, only lowercase/numbers/underscores.")
        name = prompt("Agent name")
    display_name = prompt("Display name", name.replace("_", " ").title())

    # Detect re-provision
    is_reprovision = run(["id", name], check=False).returncode == 0
    if is_reprovision:
        print()
        print(f"    ⚠ Agent '{name}' already exists — this is a RE-PROVISION.")
        print(f"    Config, .env, and SOUL.md will be overwritten.")
        print(f"    The agent service will be restarted to pick up changes.")
    print()

    # ── Step 2: Role ────────────────────────────────────────────────
    print("  ── Step 2/7: Role ──")
    role_labels = ROLE_OPTIONS + ["Custom..."]
    idx = _try_curses_single_select("Agent role", role_labels, default_index=0)
    if idx < len(ROLE_OPTIONS):
        role = ROLE_OPTIONS[idx]
    else:
        role = prompt("Custom role name")
    print(f"    Selected: {role}")
    print()

    # ── Step 3: Clearance ───────────────────────────────────────────
    print("  ── Step 3/7: DLP Clearance ──")
    clearance_labels = [f"{c[0]:10s} — {c[1]}" for c in CLEARANCE_OPTIONS]
    idx = _try_curses_single_select("Clearance level", clearance_labels, default_index=2)
    clearance = CLEARANCE_OPTIONS[idx][0]
    print(f"    Selected: {clearance}")
    print()

    # ── Step 4: AI Provider ─────────────────────────────────────────
    print("  ── Step 4/7: AI Provider ──")
    providers = get_provider_list()

    # Show top 8 + "Show all" + "Custom"
    top_items = [f"{p[1]:25s}  {p[2]}" for p in providers[:8]]
    show_all_label = f"Show all {len(providers)} providers..."
    top_items.append(show_all_label)

    idx = _try_curses_single_select("Select AI provider", top_items, default_index=0)

    if idx == len(top_items) - 1:
        # Show all providers
        all_items = [f"{p[1]:25s}  {p[2]}" for p in providers]
        idx = _try_curses_single_select("All AI providers", all_items, default_index=0)
        provider_slug = providers[idx][0]
        provider_label = providers[idx][1]
    elif idx >= len(providers):
        provider_slug = "custom"
        provider_label = "Custom / Local"
    else:
        provider_slug = providers[idx][0]
        provider_label = providers[idx][1]

    # Default model for this provider
    default_model = PROVIDER_DEFAULT_MODELS.get(provider_slug, "")
    model = prompt("Model name", default_model)
    print(f"    Provider: {provider_label} | Model: {model or '(use provider default)'}")
    print()

    # ── Step 5: AI API Keys ─────────────────────────────────────────
    print("  ── Step 5/7: API Keys ──")
    key_env = PROVIDER_KEY_ENV.get(provider_slug, "")
    base_url_default = PROVIDER_BASE_URLS.get(provider_slug, "")
    api_key = ""
    base_url = base_url_default

    if key_env:
        existing = read_existing_env(key_env)
        if existing:
            hint = f"{existing[:8]}...{existing[-4:]}"
            print(f"    Found {key_env} in ~/.hermes/.env: {hint}")
            val = prompt(f"API Key (Enter to reuse, or paste new)")
            api_key = val if val else existing
        else:
            api_key = prompt(f"API Key ({key_env})")

        if base_url_default:
            val = prompt("Base URL", base_url_default)
            base_url = val if val else base_url_default
        else:
            base_url = prompt("Base URL (leave blank for provider default)", "")
    else:
        # Custom provider or no known key
        base_url = prompt("API Base URL", "http://localhost:11434/v1")
        api_key = prompt("API Key (leave blank if none)", "")

    print(f"    Key: {'***configured***' if api_key else '(none)'}")
    print()

    # ── Step 6: Communication ───────────────────────────────────────
    print("  ── Step 6/7: Communication ──")
    platforms = discover_platforms()
    plat_labels = [p[1] for p in platforms]
    idx = _try_curses_single_select("Communication platform", plat_labels, default_index=0)
    platform_slug = platforms[idx][0]
    print(f"    Selected: {platforms[idx][1]}")

    # Platform-specific credentials
    telegram_token = ""
    allowed_users = ""
    if platform_slug == "telegram":
        telegram_token = prompt("Telegram Bot Token (from @BotFather)")
        while not telegram_token:
            print("    Token is required for Telegram.")
            telegram_token = prompt("Telegram Bot Token")
        allowed_users = prompt("Allowed Telegram User IDs (comma-separated)", "")
    else:
        # For now, most platforms need token
        telegram_token = prompt(f"{platforms[idx][1]} token/credentials", "")
    print()

    # ── Step 7: Persona ─────────────────────────────────────────────
    print("  ── Step 7/7: Persona ──")
    persona_options = [
        "Generate from template (role-based)",
        "Load from existing file",
        "Skip (write later)",
    ]
    idx = _try_curses_single_select("Agent personality", persona_options, default_index=0)
    soul_path = None
    if idx == 1:
        soul_path = prompt("Path to SOUL.md file")
        while soul_path and not Path(soul_path).exists():
            print(f"    File not found: {soul_path}")
            soul_path = prompt("Path to SOUL.md file")
    print()

    # ── MCP Servers ─────────────────────────────────────────────────
    mcps = []
    mcp_servers = discover_mcp_servers()
    if mcp_servers:
        print("  ── MCP Servers ──")
        enable = prompt("Enable MCP servers?", "n").lower()
        if enable in ("y", "yes"):
            mcp_labels = [f"{name:20s}  ({path})" for name, path in mcp_servers]
            selected = _try_curses_checklist("Select MCP servers", mcp_labels)
            mcps = [mcp_servers[i][0] for i in sorted(selected)]
        print()

    # ── Memory limit ────────────────────────────────────────────────
    memory_limit = prompt("Memory limit", "768M")
    print()

    # ── Summary ─────────────────────────────────────────────────────
    print("  ━━━ Review ━━━")
    if is_reprovision:
        print("    Mode:        RE-PROVISION (agent exists, will restart service)")
    else:
        print("    Mode:        New agent")
    print(f"    Name:        {name}")
    print(f"    Display:     {display_name}")
    print(f"    Role:        {role}")
    print(f"    Clearance:   {clearance}")
    print(f"    AI Provider: {provider_label} ({model or 'default'})")
    print(f"    Platform:    {PLATFORM_NAMES.get(platform_slug, platform_slug.title())}")
    print(f"    MCPs:        {', '.join(mcps) or 'none'}")
    print(f"    Memory:      {memory_limit}")
    print()

    confirm = prompt("Provision this agent?", "Y").lower()
    if confirm not in ("y", "yes", ""):
        print("  Cancelled.")
        sys.exit(0)

    # ── Run provisioning ────────────────────────────────────────────
    result = provision(
        name=name,
        display_name=display_name,
        role=role,
        clearance=clearance,
        soul=soul_path,
        mcps=mcps,
        telegram_token=telegram_token,
        allowed_users=allowed_users,
        glm_key=api_key,
        memory_limit=memory_limit,
        provider_slug=provider_slug,
        model=model,
        base_url=base_url,
        is_reprovision=is_reprovision,
    )

    sys.exit(0 if result["status"] == "running" else 1)


# ─── Core provisioning functions ──────────────────────────────────────

def step(n: int, total: int, msg: str) -> None:
    print(f"[{n}/{total}] {msg}")


def run(cmd: List[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)


def create_linux_user(username: str) -> None:
    """Step 1: Create Linux user for the agent."""
    result = run(["id", username], check=False)
    if result.returncode == 0:
        print(f"  User {username} already exists, skipping creation.")
        return
    run(["sudo", "useradd", "-m", "-s", "/bin/bash", username], check=True)
    print(f"  Created user: {username}")


def set_home_permissions(username: str) -> None:
    """Step 2: Set home directory permissions."""
    run(["sudo", "chmod", "750", f"/home/{username}"], check=True)
    print(f"  Set /home/{username} permissions to 750")


def create_workspace(username: str) -> None:
    """Step 3: Create workspace directory structure."""
    agent_home = Path(f"/home/{username}")
    dirs = [
        agent_home / "workspace" / "users" / "shared",
        agent_home / "workspace" / "public",
        agent_home / "workspace" / "agent" / "credentials",
        agent_home / ".hermes",
        agent_home / ".config" / "systemd" / "user",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    run(["sudo", "chown", "-R", f"{username}:{username}", str(agent_home / "workspace")], check=True)
    run(["sudo", "chown", "-R", f"{username}:{username}", str(agent_home / ".hermes")], check=True)
    run(["sudo", "chown", "-R", f"{username}:{username}", str(agent_home / ".config")], check=True)
    print(f"  Created workspace structure at /home/{username}/workspace/")


def generate_age_keypair(username: str) -> Dict[str, str]:
    """Step 4: Generate age keypair for credential encryption."""
    result = run(["which", "age-keygen"], check=False)
    if result.returncode != 0:
        print("  age-keygen not found, skipping keypair generation.")
        return {"public_key": "", "private_key": ""}

    agent_home = Path(f"/home/{username}")
    key_dir = agent_home / ".config" / "age"
    key_dir.mkdir(parents=True, exist_ok=True)

    key_file = key_dir / "keys.txt"
    result = run(["age-keygen"], check=True)
    private_key = result.stdout.strip()

    pubkey_result = run(["age-keygen", "-y"], input=result.stdout, check=True)
    public_key = pubkey_result.stdout.strip()

    key_file.write_text(private_key)
    run(["sudo", "chown", f"{username}:{username}", str(key_file)], check=True)
    os.chmod(key_file, 0o600)

    print(f"  Generated age keypair: {public_key[:20]}...")
    return {"public_key": public_key, "private_key": "stored_in_key_file"}


def generate_config(username: str, mcps: List[str], clearance: str,
                    provider_slug: str = "zai", model: str = "") -> None:
    """Step 5: Generate Hermes config.yaml."""
    agent_home = Path(f"/home/{username}")

    if not model:
        model = PROVIDER_DEFAULT_MODELS.get(provider_slug, "glm-5.1")

    # Build config dynamically
    config_lines = [
        "model:",
        f'  default: "{model}"',
        f'  provider: "{provider_slug}"',
    ]

    # Add provider-specific config
    if provider_slug == "zai":
        config_lines.append("providers:")
        config_lines.append("  zai:")
        config_lines.append("    request_timeout_seconds: 300")

    config_lines.extend([
        "",
        "agent:",
        "  max_turns: 60",
        "  verbose: false",
        "  reasoning_effort: medium",
        "",
        "session_reset:",
        "  mode: both",
        "  idle_minutes: 1440",
        "  at_hour: 4",
        "",
        "streaming:",
        "  enabled: false",
        "",
        "memory:",
        "  provider: abi_memory",
        "",
        "# Auto-approve tool execution (prevents agents from getting stuck)",
        "approvals:",
        "  mode: \"off\"",
    ])

    # Add MCP servers
    if mcps:
        config_lines.append("mcp_servers:")
        for mcp in mcps:
            config_lines.extend([
                f"  {mcp}:",
                f"    command: /opt/hermes-agent/.venv/bin/python3",
                f"    args:",
                f"      - /opt/hermes-agent/abi/mcps/servers/mcp_{mcp}/server.py",
                f"    transport: stdio",
                f"    env:",
                f"      PYTHONPATH: /opt/hermes-agent",
            ])

    config = "\n".join(config_lines) + "\n"
    config_file = agent_home / ".hermes" / "config.yaml"
    config_file.write_text(config)
    run(["sudo", "chown", f"{username}:{username}", str(config_file)], check=True)
    print(f"  Written config.yaml ({provider_slug}/{model}, {len(mcps)} MCP server(s))")


def write_env(username: str, api_key: str, telegram_token: str,
              allowed_users: str = "", provider_slug: str = "zai",
              base_url: str = "") -> None:
    """Step 6: Write .env file with secrets."""
    agent_home = Path(f"/home/{username}")

    # Build env vars dynamically based on provider
    env_lines = []

    # API key
    key_env = PROVIDER_KEY_ENV.get(provider_slug, "")
    if key_env and api_key:
        env_lines.append(f"{key_env}={api_key}")

    # Base URL
    if base_url:
        # Determine the base URL env var name
        base_env = "GLM_BASE_URL" if provider_slug == "zai" else f"{provider_slug.upper()}_BASE_URL"
        env_lines.append(f"{base_env}={base_url}")

    # Telegram
    env_lines.append(f"TELEGRAM_BOT_TOKEN={telegram_token}")
    if allowed_users:
        env_lines.append(f"TELEGRAM_ALLOWED_USERS={allowed_users}")

    env = "\n".join(env_lines) + "\n"
    env_file = agent_home / ".hermes" / ".env"
    env_file.write_text(env)
    run(["sudo", "chown", f"{username}:{username}", str(env_file)], check=True)
    os.chmod(env_file, 0o600)
    print(f"  Written .env (mode 600)")


def write_soul(username: str, display_name: str, role: str, soul_path: Optional[str] = None) -> None:
    """Step 7: Write SOUL.md."""
    agent_home = Path(f"/home/{username}")

    if soul_path:
        soul_content = Path(soul_path).read_text()
    else:
        role_descriptions = {
            "CEO": "the AI CEO of the organization",
            "CTO": "the technical lead of the organization",
            "Marketing": "the marketing lead of the organization",
            "Sales": "the sales lead of the organization",
            "Ops": "the operations manager of the organization",
            "Finance": "the finance manager of the organization",
        }
        role_desc = role_descriptions.get(role, f"a {role} agent")
        soul_content = SOUL_TEMPLATE.format(display_name=display_name, role_description=role_desc)

    soul_file = agent_home / ".hermes" / "SOUL.md"
    soul_file.write_text(soul_content)
    run(["sudo", "chown", f"{username}:{username}", str(soul_file)], check=True)
    print(f"  Written SOUL.md ({len(soul_content)} bytes)")


def create_db_role(username: str) -> None:
    """Step 8: Create PostgreSQL role for the agent (optional)."""
    dsn = f"postgresql://{DB_ADMIN_USER}:{DB_ADMIN_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    result = run(
        ["psql", dsn, "-c", f"SELECT 1 FROM pg_roles WHERE rolname = '{username}'"],
        check=False,
    )
    if "1 row" in result.stdout:
        print(f"  DB role {username} already exists, skipping.")
        return
    result = run(
        ["psql", dsn, "-c", f"CREATE ROLE {username} WITH LOGIN PASSWORD '{_random_password()}'"],
        check=False,
    )
    if result.returncode == 0:
        print(f"  Created DB role: {username}")
    else:
        print(f"  DB role creation skipped: {result.stderr.strip()}")


def insert_agent_record(username: str, display_name: str, role: str,
                        clearance: str, bot_username: str = "") -> None:
    """Step 9: Insert agent record in database."""
    dsn = f"postgresql://{DB_ADMIN_USER}:{DB_ADMIN_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    run([
        "psql", dsn, "-c",
        """CREATE TABLE IF NOT EXISTS abi_agents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            clearance TEXT NOT NULL DEFAULT 'external',
            telegram_bot TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )"""
    ], check=False)

    run([
        "psql", dsn, "-c",
        f"INSERT INTO abi_agents (username, display_name, role, clearance, telegram_bot) "
        f"VALUES ('{username}', '{display_name}', '{role}', '{clearance}', '{bot_username}') "
        f"ON CONFLICT (username) DO UPDATE SET display_name='{display_name}', role='{role}', clearance='{clearance}'"
    ], check=False)
    print(f"  Agent record in database: {display_name} ({clearance})")


def setup_systemd(username: str, display_name: str, memory_limit: str = "768M") -> None:
    """Step 10: Generate and install systemd service."""
    service_content = generate_service(username, display_name, memory_limit)
    msg = install_service(username, service_content)
    print(f"  {msg}")


def enable_and_start(username: str) -> None:
    """Step 11: Enable and start the systemd service."""
    msg = start_service(username)
    print(f"  {msg}")


def verify_agent(username: str, telegram_token: str) -> bool:
    """Step 12: Verify the agent is running."""
    import time
    time.sleep(3)

    status = get_service_status(username)
    if "active (running)" in status:
        print(f"  Agent is running!")
        bot_info = verify_bot_token(telegram_token)
        if bot_info:
            print(f"  Telegram bot: @{bot_info.get('username', 'unknown')}")
        return True
    else:
        print(f"  Agent may not be running. Status:")
        print(f"  {status[:200]}")
        return False


def _random_password(length: int = 24) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def provision(
    name: str,
    display_name: str,
    role: str = "assistant",
    clearance: str = "external",
    soul: Optional[str] = None,
    mcps: Optional[List[str]] = None,
    telegram_token: Optional[str] = None,
    allowed_users: Optional[str] = None,
    glm_key: Optional[str] = None,
    memory_limit: str = "768M",
    skip_db: bool = False,
    provider_slug: str = "zai",
    model: str = "",
    base_url: str = "",
    is_reprovision: bool = False,
) -> Dict:
    """Full agent provisioning (12 steps).

    Args:
        name: Linux username for the agent (lowercase, no spaces).
        display_name: Human-readable name.
        role: Agent role (CEO, Marketing, etc.).
        clearance: DLP clearance level (admin, internal, external).
        soul: Path to SOUL.md file.
        mcps: List of MCP server names to enable.
        telegram_token: Telegram bot token.
        allowed_users: Comma-separated Telegram user IDs.
        glm_key: API key (any provider, not just GLM).
        memory_limit: systemd memory limit.
        skip_db: Skip PostgreSQL role and agent record.
        provider_slug: AI provider (zai, anthropic, etc.).
        model: Model name override.
        base_url: API base URL override.
        is_reprovision: True if agent already exists (triggers restart).

    Returns:
        Dict with provisioning results.
    """
    total_steps = 12
    mcps = mcps or []

    # Resolve API key from existing env if not provided
    if not glm_key:
        key_env = PROVIDER_KEY_ENV.get(provider_slug, "GLM_API_KEY")
        glm_key = read_existing_env(key_env) or ""
    if not glm_key and provider_slug != "custom":
        # Try GLM_API_KEY as last resort
        glm_key = read_existing_env("GLM_API_KEY") or ""
    if not glm_key:
        raise ProvisioningError(
            f"API key required. Set --glm-key or have {PROVIDER_KEY_ENV.get(provider_slug, 'API_KEY')} in ~/.hermes/.env"
        )

    if not telegram_token:
        raise ProvisioningError("Communication token required")

    # Configure Telegram bot
    bot_result = configure_agent_bot(telegram_token, display_name)
    bot_username = ""
    if bot_result.get("ok"):
        bot_username = bot_result.get("bot_username", "")
        print(f"Telegram bot configured: @{bot_username}")
    else:
        print(f"Warning: Telegram bot setup failed: {bot_result.get('error', 'unknown')}")

    print(f"\nProvisioning agent: {display_name} ({name})")
    print(f"  Role: {role} | Clearance: {clearance} | Provider: {provider_slug} | MCPs: {', '.join(mcps) or 'none'}")
    print()

    step(1, total_steps, "Creating Linux user")
    create_linux_user(name)

    step(2, total_steps, "Setting home permissions")
    set_home_permissions(name)

    step(3, total_steps, "Creating workspace structure")
    create_workspace(name)

    step(4, total_steps, "Generating age keypair")
    age_keys = generate_age_keypair(name)

    step(5, total_steps, "Generating config.yaml")
    generate_config(name, mcps, clearance, provider_slug, model)

    step(6, total_steps, "Writing .env")
    write_env(name, glm_key, telegram_token, allowed_users or "", provider_slug, base_url)

    step(7, total_steps, "Writing SOUL.md")
    write_soul(name, display_name, role, soul)

    if not skip_db:
        step(8, total_steps, "Creating PostgreSQL role")
        create_db_role(name)

        step(9, total_steps, "Inserting agent record")
        insert_agent_record(name, display_name, role, clearance, bot_username)
    else:
        print("[8-9/12] Skipping database setup (--skip-db)")

    step(10, total_steps, "Installing systemd service")
    setup_systemd(name, display_name, memory_limit)

    step(11, total_steps, "Starting service")
    if is_reprovision:
        # For re-provision: clear pycache and restart to pick up new config
        print("  Re-provision detected: clearing caches and restarting...")
        run(["find", "/opt/hermes-agent/abi", "-name", "__pycache__", "-type", "d", "-exec", "rm", "-rf", "{}", "+"], check=False)
        # Get UID for the user
        uid_result = run(["id", "-u", name], check=True)
        uid = uid_result.stdout.strip()
        run([
            "sudo", "-u", name,
            f"XDG_RUNTIME_DIR=/run/user/{uid}",
            "systemctl", "--user", "restart", "abi-agent.service",
        ], check=False)
        print("  Service restarted.")
    else:
        enable_and_start(name)

    step(12, total_steps, "Verifying")
    ok = verify_agent(name, telegram_token)

    print()
    if ok:
        print(f"Agent '{display_name}' provisioned successfully!")
        print(f"  User: {name}")
        print(f"  Home: /home/{name}")
        print(f"  Config: /home/{name}/.hermes/config.yaml")
        print(f"  Telegram: @{bot_username}")
        print(f"  Provider: {provider_slug} ({model or 'default'})")
        print(f"  MCPs: {', '.join(mcps) or 'none'}")
    else:
        print(f"Agent '{display_name}' provisioned but verification failed. Check service status.")

    return {
        "username": name,
        "display_name": display_name,
        "bot_username": bot_username,
        "status": "running" if ok else "needs_attention",
    }


def main():
    parser = argparse.ArgumentParser(description="Provision a new ABI agent")
    parser.add_argument("--name", help="Linux username (lowercase, no spaces)")
    parser.add_argument("--display-name", help="Human-readable agent name")
    parser.add_argument("--role", default="assistant", help="Agent role (CEO, Marketing, etc.)")
    parser.add_argument("--clearance", default="external", choices=["admin", "internal", "external"])
    parser.add_argument("--soul", help="Path to SOUL.md file")
    parser.add_argument("--mcps", default="", help="Comma-separated MCP server names")
    parser.add_argument("--telegram-token", help="Telegram bot token")
    parser.add_argument("--allowed-users", default="", help="Comma-separated Telegram user IDs")
    parser.add_argument("--glm-key", help="API key (default: read from current .env)")
    parser.add_argument("--provider", default="zai", help="AI provider slug (zai, anthropic, etc.)")
    parser.add_argument("--model", default="", help="Model name override")
    parser.add_argument("--base-url", default="", help="API base URL override")
    parser.add_argument("--memory-limit", default="768M", help="systemd memory limit")
    parser.add_argument("--skip-db", action="store_true", help="Skip PostgreSQL setup")
    parser.add_argument("--interactive", action="store_true", help="Force interactive mode")
    args = parser.parse_args()

    # Determine if we should run interactively
    has_required = args.name and args.display_name and args.telegram_token
    force_interactive = args.interactive or not has_required

    if force_interactive:
        interactive_main()
        return

    # Non-interactive: all required args provided via CLI
    mcps = [m.strip() for m in args.mcps.split(",") if m.strip()] if args.mcps else []

    result = provision(
        name=args.name,
        display_name=args.display_name,
        role=args.role,
        clearance=args.clearance,
        soul=args.soul,
        mcps=mcps,
        telegram_token=args.telegram_token,
        allowed_users=args.allowed_users,
        glm_key=args.glm_key,
        memory_limit=args.memory_limit,
        skip_db=args.skip_db,
        provider_slug=args.provider,
        model=args.model,
        base_url=args.base_url,
    )

    sys.exit(0 if result["status"] == "running" else 1)


if __name__ == "__main__":
    main()
