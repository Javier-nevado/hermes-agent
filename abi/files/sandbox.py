"""Per-user file sandbox — path resolver with namespace isolation.

Each agent's workspace is organized as:
    /home/{agent_user}/workspace/
    ├── users/
    │   ├── {user_id}/    ← Private per-user files
    │   └── shared/       ← Explicitly shared between users
    ├── public/           ← Available to all users of this agent
    └── agent/            ← Agent's own operational files

Path resolution rules:
    "report.pdf"                  → users/{user_id}/report.pdf
    "shared/deck.pdf"             → users/shared/deck.pdf
    "public/faq.md"               → public/faq.md
    "agent/config.json"           → agent/config.json
    Absolute paths                → BLOCKED
    "../" traversal               → BLOCKED
    Symlinks outside workspace    → BLOCKED
"""

import os
from pathlib import Path
from typing import Optional


def resolve_path(
    requested: str,
    user_id: str,
    agent_home: str,
) -> str:
    """Resolve a requested file path to an absolute path within the sandbox.

    Args:
        requested: The path the user/agent requested (relative).
        user_id: The current user's identifier.
        agent_home: The agent's home directory (HERMES_HOME parent).

    Returns:
        Absolute resolved path.

    Raises:
        ValueError: If path violates sandbox rules.
    """
    workspace = Path(agent_home) / "workspace"
    workspace = workspace.resolve()

    # Block absolute paths
    if os.path.isabs(requested):
        raise ValueError(f"Absolute paths not allowed: {requested}")

    # Block path traversal
    if ".." in Path(requested).parts:
        raise ValueError(f"Path traversal not allowed: {requested}")

    # Normalize the path
    clean = os.path.normpath(requested)
    if clean.startswith(".."):
        raise ValueError(f"Path traversal not allowed: {requested}")

    # Route to the correct namespace
    parts = Path(clean).parts
    if len(parts) >= 1 and parts[0] == "shared":
        # shared/ prefix → workspace/users/shared/
        target = workspace / "users" / clean
    elif len(parts) >= 1 and parts[0] == "public":
        # public/ prefix → workspace/public/
        target = workspace / clean
    elif len(parts) >= 1 and parts[0] == "agent":
        # agent/ prefix → workspace/agent/
        target = workspace / clean
    else:
        # Default: user's private namespace
        target = workspace / "users" / user_id / clean

    # Final safety check: ensure resolved path is within workspace
    try:
        target_resolved = target.resolve()
        if not str(target_resolved).startswith(str(workspace)):
            raise ValueError(f"Path escapes workspace: {requested}")
    except (OSError, ValueError):
        raise ValueError(f"Invalid path: {requested}")

    return str(target_resolved)


def get_user_workspace_dir(user_id: str, agent_home: str) -> str:
    """Return the user's private workspace directory (for TERMINAL_CWD)."""
    return str(Path(agent_home) / "workspace" / "users" / user_id)


def create_workspace_structure(agent_home: str) -> None:
    """Create the full workspace directory structure for an agent."""
    workspace = Path(agent_home) / "workspace"
    dirs = [
        workspace / "users" / "shared",
        workspace / "public",
        workspace / "agent",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def list_user_files(user_id: str, agent_home: str, namespace: str = "private") -> list[str]:
    """List files in a user's namespace.

    Args:
        user_id: The user identifier.
        agent_home: Agent home directory.
        namespace: "private", "shared", "public", or "agent".

    Returns:
        List of relative file paths.
    """
    workspace = Path(agent_home) / "workspace"

    if namespace == "private":
        base = workspace / "users" / user_id
    elif namespace == "shared":
        base = workspace / "users" / "shared"
    elif namespace == "public":
        base = workspace / "public"
    elif namespace == "agent":
        base = workspace / "agent"
    else:
        raise ValueError(f"Unknown namespace: {namespace}")

    if not base.exists():
        return []

    files = []
    for f in sorted(base.rglob("*")):
        if f.is_file():
            files.append(str(f.relative_to(base)))
    return files
