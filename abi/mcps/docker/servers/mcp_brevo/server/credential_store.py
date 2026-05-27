"""Credential store wrapper for Docker-containerized MCP servers.

Reads credentials from the mounted volume (/credentials/{service}.json)
instead of the agent home directory. This is a lightweight version that
only supports read operations — credential writes (login/logout) are
handled by the agent-side MCP server, not the container.

For Docker mode, agents write credentials to their workspace/agent/credentials/
directory, which is mounted read-only into the container.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _credentials_dir() -> Path:
    """Get the credentials directory from mounted volume or agent home."""
    cred_path = os.environ.get("CREDENTIALS_PATH", "")
    if cred_path:
        return Path(cred_path)
    # Fallback: HERMES_HOME-based path (for non-Docker usage)
    hermes_home = os.environ.get("HERMES_HOME", "")
    if hermes_home:
        agent_home = str(Path(hermes_home).parent)
    else:
        agent_home = str(Path.home())
    return Path(agent_home) / "workspace/agent/credentials"


def get_credentials(service: str) -> Optional[Dict[str, Any]]:
    """Read stored credentials for a service."""
    cred_file = _credentials_dir() / f"{service}.json"
    if not cred_file.exists():
        return None
    try:
        data = json.loads(cred_file.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(service: str, credentials: Dict[str, Any]) -> None:
    """Store credentials — in Docker mode, this writes to mounted volume."""
    cred_dir = _credentials_dir()
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_file = cred_dir / f"{service}.json"
    cred_file.write_text(json.dumps(credentials, indent=2))
    try:
        os.chmod(cred_file, 0o600)
    except OSError:
        pass  # Read-only mount in Docker — expected


def delete_credentials(service: str) -> bool:
    """Delete stored credentials."""
    cred_file = _credentials_dir() / f"{service}.json"
    if cred_file.exists():
        try:
            cred_file.unlink()
            return True
        except OSError:
            return False  # Read-only mount
    return False


def has_credentials(service: str) -> bool:
    """Check if credentials exist for a service."""
    return (_credentials_dir() / f"{service}.json").exists()
