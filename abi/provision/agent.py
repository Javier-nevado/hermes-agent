"""ABI Agent Provisioning Orchestrator.

One-command agent creation: Linux user → workspace → config → systemd → verify.

Usage:
    sudo python3 scripts/abi-provision \\
        --name ailean \\
        --display-name "AIlean" \\
        --role CEO \\
        --clearance admin \\
        --soul /path/to/soul.md \\
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
import secrets
import string
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Add abi/ to path
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

# Config templates
CONFIG_TEMPLATE = """model:
  default: "glm-5.1"
  provider: "zai"

agent:
  max_turns: 60
  verbose: false
  reasoning_effort: "medium"

session_reset:
  mode: both
  idle_minutes: 1440
  at_hour: 4

streaming:
  enabled: false

memory:
  provider: abi_memory
{mcp_section}
"""

MCP_SERVER_TEMPLATE = """
  {name}:
    command: /opt/hermes-agent/.venv/bin/python3
    args: ["/opt/hermes-agent/abi/mcps/servers/mcp_{name}/server.py"]
    transport: stdio
    env:
      PYTHONPATH: /opt/hermes-agent
"""

ENV_TEMPLATE = """GLM_API_KEY={glm_key}
GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4/
TELEGRAM_BOT_TOKEN={telegram_token}
TELEGRAM_ALLOWED_USERS={allowed_users}
"""

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


def step(n: int, total: int, msg: str) -> None:
    print(f"[{n}/{total}] {msg}")


def run(cmd: List[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)


def create_linux_user(username: str) -> None:
    """Step 1: Create Linux user for the agent."""
    # Check if user exists
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

    # Set ownership
    run(["sudo", "chown", "-R", f"{username}:{username}", str(agent_home / "workspace")], check=True)
    run(["sudo", "chown", "-R", f"{username}:{username}", str(agent_home / ".hermes")], check=True)
    run(["sudo", "chown", "-R", f"{username}:{username}", str(agent_home / ".config")], check=True)
    print(f"  Created workspace structure at /home/{username}/workspace/")


def generate_age_keypair(username: str) -> Dict[str, str]:
    """Step 4: Generate age keypair for credential encryption."""
    # Check if age is installed
    result = run(["which", "age-keygen"], check=False)
    if result.returncode != 0:
        print("  age-keygen not found, skipping keypair generation (install age for Phase 7).")
        return {"public_key": "", "private_key": ""}

    agent_home = Path(f"/home/{username}")
    key_dir = agent_home / ".config" / "age"
    key_dir.mkdir(parents=True, exist_ok=True)

    key_file = key_dir / "keys.txt"
    result = run(["age-keygen"], check=True)
    private_key = result.stdout.strip()

    # Extract public key
    pubkey_result = run(["age-keygen", "-y"], input=result.stdout, check=True)
    public_key = pubkey_result.stdout.strip()

    key_file.write_text(private_key)
    run(["sudo", "chown", f"{username}:{username}", str(key_file)], check=True)
    os.chmod(key_file, 0o600)

    print(f"  Generated age keypair: {public_key[:20]}...")
    return {"public_key": public_key, "private_key": "stored_in_key_file"}


def generate_config(username: str, mcps: List[str], clearance: str, agent_name: str) -> None:
    """Step 5: Generate Hermes config.yaml."""
    agent_home = Path(f"/home/{username}")

    # Build MCP section
    mcp_section = ""
    if mcps:
        mcp_lines = ["mcp_servers:"]
        for mcp in mcps:
            mcp_lines.append("  " + MCP_SERVER_TEMPLATE.format(name=mcp).strip())
        mcp_section = "\n" + "\n".join(mcp_lines) + "\n"

    config = CONFIG_TEMPLATE.format(mcp_section=mcp_section)

    config_file = agent_home / ".hermes" / "config.yaml"
    config_file.write_text(config)
    run(["sudo", "chown", f"{username}:{username}", str(config_file)], check=True)
    print(f"  Written config.yaml with {len(mcps)} MCP server(s)")


def write_env(username: str, glm_key: str, telegram_token: str, allowed_users: str = "") -> None:
    """Step 6: Write .env file with secrets."""
    agent_home = Path(f"/home/{username}")

    env = ENV_TEMPLATE.format(
        glm_key=glm_key,
        telegram_token=telegram_token,
        allowed_users=allowed_users,
    )

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


def insert_agent_record(username: str, display_name: str, role: str, clearance: str, bot_username: str = "") -> None:
    """Step 9: Insert agent record in database."""
    dsn = f"postgresql://{DB_ADMIN_USER}:{DB_ADMIN_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    # Create agents table if not exists
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
    time.sleep(3)  # Give service time to start

    status = get_service_status(username)
    if "active (running)" in status:
        print(f"  Agent is running!")

        # Verify Telegram bot
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
        glm_key: GLM API key (shared default if not specified).
        memory_limit: systemd memory limit.
        skip_db: Skip PostgreSQL role and agent record.

    Returns:
        Dict with provisioning results.
    """
    total_steps = 12
    mcps = mcps or []

    # Resolve GLM key
    if not glm_key:
        # Read from current agent's .env
        default_env = Path.home() / ".hermes" / ".env"
        if default_env.exists():
            for line in default_env.read_text().splitlines():
                if line.startswith("GLM_API_KEY="):
                    glm_key = line.split("=", 1)[1]
                    break
    if not glm_key:
        raise ProvisioningError("GLM_API_KEY required (set --glm-key or have it in ~/.hermes/.env)")

    if not telegram_token:
        raise ProvisioningError("Telegram bot token required (--telegram-token)")

    # Configure Telegram bot
    bot_result = configure_agent_bot(telegram_token, display_name)
    bot_username = ""
    if bot_result.get("ok"):
        bot_username = bot_result.get("bot_username", "")
        print(f"Telegram bot configured: @{bot_username}")
    else:
        print(f"Warning: Telegram bot setup failed: {bot_result.get('error', 'unknown')}")

    print(f"\nProvisioning agent: {display_name} ({name})")
    print(f"  Role: {role} | Clearance: {clearance} | MCPs: {', '.join(mcps) or 'none'}")
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
    generate_config(name, mcps, clearance, name)

    step(6, total_steps, "Writing .env")
    write_env(name, glm_key, telegram_token, allowed_users or "")

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
    parser.add_argument("--name", required=True, help="Linux username (lowercase, no spaces)")
    parser.add_argument("--display-name", required=True, help="Human-readable agent name")
    parser.add_argument("--role", default="assistant", help="Agent role (CEO, Marketing, etc.)")
    parser.add_argument("--clearance", default="external", choices=["admin", "internal", "external"])
    parser.add_argument("--soul", help="Path to SOUL.md file")
    parser.add_argument("--mcps", default="", help="Comma-separated MCP server names")
    parser.add_argument("--telegram-token", required=True, help="Telegram bot token")
    parser.add_argument("--allowed-users", default="", help="Comma-separated Telegram user IDs")
    parser.add_argument("--glm-key", help="GLM API key (default: read from current .env)")
    parser.add_argument("--memory-limit", default="768M", help="systemd memory limit")
    parser.add_argument("--skip-db", action="store_true", help="Skip PostgreSQL setup")
    args = parser.parse_args()

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
    )

    sys.exit(0 if result["status"] == "running" else 1)


if __name__ == "__main__":
    main()
