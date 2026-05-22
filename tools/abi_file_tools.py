"""ABI File Tools — custom Hermes tools for multi-user file operations.

These tools extend Hermes' built-in file tools with cross-namespace
operations: sharing, public access, and user-scoped listing.

The built-in file tools (read_file, write_file) still work for
within-namespace operations — TERMINAL_CWD routes them to the
correct user directory.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

# Tool schemas
SHARE_FILE_SCHEMA = {
    "name": "abi_share_file",
    "description": (
        "Share a file from your private namespace with another user. "
        "The file is copied to their private namespace. "
        "Use 'make_public' instead to share with all users."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File to share (relative path in your namespace).",
            },
            "target_user": {
                "type": "string",
                "description": "User ID to share with.",
            },
        },
        "required": ["filename", "target_user"],
    },
}

MAKE_PUBLIC_SCHEMA = {
    "name": "abi_make_public",
    "description": "Copy a file from your private namespace to the public directory, making it available to all users of this agent.",
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File to make public (relative path in your namespace).",
            },
        },
        "required": ["filename"],
    },
}

LIST_FILES_SCHEMA = {
    "name": "abi_list_files",
    "description": "List files in a specific namespace (private, shared, public, or agent).",
    "parameters": {
        "type": "object",
        "properties": {
            "namespace": {
                "type": "string",
                "enum": ["private", "shared", "public", "agent"],
                "description": "Which namespace to list (default: private).",
                "default": "private",
            },
        },
        "required": [],
    },
}


def get_user_id() -> str:
    """Get current user ID from Hermes session context."""
    try:
        from gateway.session_context import get_session_env
        env = get_session_env()
        uid = env.get("HERMES_SESSION_USER_ID", "")
        if uid and uid != "<UNSET>":
            return uid
    except Exception:
        pass
    return "default"


def get_agent_home() -> str:
    """Get agent home directory."""
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        # HERMES_HOME is ~/.hermes, agent home is one level up
        return str(home.parent)
    except Exception:
        return str(Path.home())


def handle_share_file(args: Dict[str, Any]) -> str:
    """Share a file with another user."""
    from abi.files.sharing import share_with

    user_id = get_user_id()
    agent_home = get_agent_home()

    try:
        target = share_with(
            filename=args["filename"],
            source_user_id=user_id,
            target_user_id=args["target_user"],
            agent_home=agent_home,
        )
        return json.dumps({"status": "shared", "path": target})
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"Share failed: {e}"})


def handle_make_public(args: Dict[str, Any]) -> str:
    """Make a file publicly available."""
    from abi.files.sharing import make_public

    user_id = get_user_id()
    agent_home = get_agent_home()

    try:
        target = make_public(
            filename=args["filename"],
            user_id=user_id,
            agent_home=agent_home,
        )
        return json.dumps({"status": "public", "path": target})
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"Make public failed: {e}"})


def handle_list_files(args: Dict[str, Any]) -> str:
    """List files in a namespace."""
    from abi.files.sandbox import list_user_files

    user_id = get_user_id()
    agent_home = get_agent_home()
    namespace = args.get("namespace", "private")

    try:
        files = list_user_files(user_id, agent_home, namespace)
        return json.dumps({"namespace": namespace, "files": files, "count": len(files)})
    except Exception as e:
        return json.dumps({"error": f"List failed: {e}"})


# Map tool names to handlers
TOOL_SCHEMAS = [SHARE_FILE_SCHEMA, MAKE_PUBLIC_SCHEMA, LIST_FILES_SCHEMA]
TOOL_HANDLERS = {
    "abi_share_file": handle_share_file,
    "abi_make_public": handle_make_public,
    "abi_list_files": handle_list_files,
}
