"""File sharing mechanisms between user namespaces.

- share_with(file, target_user): Copy from current user to another user
- make_public(file): Copy to public/ directory
- revoke_share(file, target_user): Remove shared copy
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from .sandbox import resolve_path, list_user_files


def share_with(
    filename: str,
    source_user_id: str,
    target_user_id: str,
    agent_home: str,
    symlink: bool = False,
) -> str:
    """Share a file from one user's namespace to another.

    Args:
        filename: File to share (relative path in source user's namespace).
        source_user_id: User who owns the file.
        target_user_id: User to share with.
        agent_home: Agent home directory.
        symlink: Use symlink instead of copy (for large files).

    Returns:
        Path to the shared file in target user's namespace.
    """
    source_path = resolve_path(filename, source_user_id, agent_home)

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"File not found: {filename}")

    workspace = Path(agent_home) / "workspace"
    target_dir = workspace / "users" / target_user_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    # Create parent directories
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if symlink:
        if target_path.exists() or target_path.is_symlink():
            target_path.unlink()
        os.symlink(str(source_path), str(target_path))
    else:
        shutil.copy2(source_path, str(target_path))

    return str(target_path)


def make_public(
    filename: str,
    user_id: str,
    agent_home: str,
) -> str:
    """Copy a file from user's private namespace to public/.

    Returns:
        Path to the public file.
    """
    source_path = resolve_path(filename, user_id, agent_home)

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"File not found: {filename}")

    public_dir = Path(agent_home) / "workspace" / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    target_path = public_dir / filename
    target_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_path, str(target_path))
    return str(target_path)


def revoke_share(
    filename: str,
    target_user_id: str,
    agent_home: str,
) -> bool:
    """Remove a shared file from a target user's namespace."""
    workspace = Path(agent_home) / "workspace"
    target_path = workspace / "users" / target_user_id / filename

    if target_path.exists() or target_path.is_symlink():
        target_path.unlink()
        return True
    return False
