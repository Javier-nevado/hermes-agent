"""Credential store — per-agent credential routing for Docker MCP containers.

Supports multi-tenancy: agents connect to the same Docker container but
get their own credentials based on the X-Agent-Id header.

Resolution order:
  1. /credentials/{agent_id}/{service}.json  (per-agent)
  2. /credentials/_shared/{service}.json     (shared fallback)
  3. /credentials/{service}.json             (legacy flat layout)

Environment:
  CREDENTIALS_PATH — Root of credential tree (default: /credentials)
"""

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Optional

# Set by the HTTP adapter middleware from X-Agent-Id header
current_agent: ContextVar[str] = ContextVar("current_agent", default="")


def _credentials_root() -> Path:
    """Get the root of the credential tree."""
    cred_path = os.environ.get("CREDENTIALS_PATH", "")
    if cred_path:
        return Path(cred_path)
    hermes_home = os.environ.get("HERMES_HOME", "")
    if hermes_home:
        return Path(hermes_home).parent / "workspace" / "agent" / "credentials"
    return Path.home() / "workspace" / "agent" / "credentials"


def _resolve_cred_file(service: str) -> Optional[Path]:
    """Resolve credential file for the current agent + service."""
    root = _credentials_root()
    agent = current_agent.get("")

    if agent:
        # Per-agent credentials (highest priority)
        agent_file = root / agent / f"{service}.json"
        if agent_file.exists():
            return agent_file

    # Shared fallback (agent-agnostic)
    shared_file = root / "_shared" / f"{service}.json"
    if shared_file.exists():
        return shared_file

    # Legacy flat layout (backward compat)
    flat_file = root / f"{service}.json"
    if flat_file.exists():
        return flat_file

    return None


def get_credentials(service: str) -> Optional[Dict[str, Any]]:
    """Read stored credentials for a service, routed by agent identity."""
    cred_file = _resolve_cred_file(service)
    if cred_file is None:
        return None
    try:
        return json.loads(cred_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(service: str, credentials: Dict[str, Any]) -> None:
    """Store credentials for the current agent + service."""
    root = _credentials_root()
    agent = current_agent.get("")
    if agent:
        cred_dir = root / agent
    else:
        cred_dir = root / "_shared"
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_file = cred_dir / f"{service}.json"
    cred_file.write_text(json.dumps(credentials, indent=2))
    try:
        os.chmod(cred_file, 0o600)
    except OSError:
        pass  # Read-only mount in Docker — expected


def delete_credentials(service: str) -> bool:
    """Delete stored credentials for the current agent + service."""
    cred_file = _resolve_cred_file(service)
    if cred_file and cred_file.exists():
        try:
            cred_file.unlink()
            return True
        except OSError:
            return False
    return False


def has_credentials(service: str) -> bool:
    """Check if credentials exist for the current agent + service."""
    return _resolve_cred_file(service) is not None
