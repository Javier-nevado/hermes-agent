"""Credential store — per-agent encrypted credential storage.

Each agent stores credentials in /home/{agent_user}/.abi/credentials/
as JSON files. For the MVP, files are stored with mode 600 (owner-only).
Full sops+age encryption will be added in Phase 7 (Hardening).

MCP servers read/write credentials through this module.
The agent's home directory is determined from the MCP server's
working directory or HERMES_HOME environment variable.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _credentials_dir() -> Path:
    """Get the credentials directory for the current agent."""
    # HERMES_HOME is set by Hermes when launching MCP servers
    hermes_home = os.environ.get("HERMES_HOME", "")
    if hermes_home:
        agent_home = str(Path(hermes_home).parent)
    else:
        agent_home = str(Path.home())

    cred_dir = Path(agent_home) / ".abi" / "credentials"
    cred_dir.mkdir(parents=True, exist_ok=True)
    return cred_dir


def get_credentials(service: str) -> Optional[Dict[str, Any]]:
    """Read stored credentials for a service.

    Args:
        service: Service name (e.g., "brevo", "m365").

    Returns:
        Credential dict or None if not configured.
    """
    cred_file = _credentials_dir() / f"{service}.json"
    if not cred_file.exists():
        return None

    try:
        data = json.loads(cred_file.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(service: str, credentials: Dict[str, Any]) -> None:
    """Store credentials for a service.

    Args:
        service: Service name.
        credentials: Credential data to store.
    """
    cred_file = _credentials_dir() / f"{service}.json"
    cred_file.write_text(json.dumps(credentials, indent=2))
    # Owner-only permissions
    os.chmod(cred_file, 0o600)


def delete_credentials(service: str) -> bool:
    """Delete stored credentials for a service.

    Returns:
        True if credentials were deleted, False if not found.
    """
    cred_file = _credentials_dir() / f"{service}.json"
    if cred_file.exists():
        cred_file.unlink()
        return True
    return False


def has_credentials(service: str) -> bool:
    """Check if credentials exist for a service."""
    return (_credentials_dir() / f"{service}.json").exists()
