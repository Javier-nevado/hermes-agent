"""Telegram bot creation helper for ABI agents.

Interacts with the Telegram Bot API to:
- Verify a bot token is valid
- Set the bot's name and description
- Configure the bot's commands
"""

import json
import urllib.request
from typing import Dict, List, Optional


TG_API = "https://api.telegram.org"


def verify_bot_token(token: str) -> Optional[Dict]:
    """Verify a Telegram bot token is valid.

    Args:
        token: Telegram bot token.

    Returns:
        Bot info dict if valid, None if invalid.
    """
    try:
        req = urllib.request.Request(f"{TG_API}/bot{token}/getMe")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return data.get("result")
    except Exception:
        pass
    return None


def set_bot_name(token: str, name: str) -> bool:
    """Set the bot's display name.

    Args:
        token: Bot token.
        name: Display name.

    Returns:
        True if successful.
    """
    try:
        data = json.dumps({"name": name}).encode()
        req = urllib.request.Request(
            f"{TG_API}/bot{token}/setMyName",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False


def set_bot_description(token: str, description: str) -> bool:
    """Set the bot's description (shown in profile).

    Args:
        token: Bot token.
        description: Bot description.

    Returns:
        True if successful.
    """
    try:
        data = json.dumps({"description": description}).encode()
        req = urllib.request.Request(
            f"{TG_API}/bot{token}/setMyDescription",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False


def set_bot_commands(token: str, commands: List[Dict[str, str]]) -> bool:
    """Set the bot's command list.

    Args:
        token: Bot token.
        commands: List of {"command": "...", "description": "..."} dicts.

    Returns:
        True if successful.
    """
    try:
        data = json.dumps({"commands": commands}).encode()
        req = urllib.request.Request(
            f"{TG_API}/bot{token}/setMyCommands",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False


def configure_agent_bot(token: str, display_name: str) -> Dict:
    """Full bot setup: verify, set name, set description, set commands.

    Args:
        token: Bot token.
        display_name: Agent display name.

    Returns:
        Dict with setup results.
    """
    bot_info = verify_bot_token(token)
    if not bot_info:
        return {"error": "Invalid bot token"}

    username = bot_info.get("username", "")

    set_bot_name(token, display_name)
    set_bot_description(token, f"I'm {display_name}, your AI business assistant powered by ABI.")
    set_bot_commands(token, [
        {"command": "start", "description": "Start a conversation"},
        {"command": "reset", "description": "Reset conversation context"},
        {"command": "status", "description": "Check agent status"},
    ])

    return {
        "ok": True,
        "bot_username": username,
        "bot_name": display_name,
    }
