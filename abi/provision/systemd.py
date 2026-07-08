"""Systemd service unit generator for ABI agents.
"""

import os
import secrets
import string
import subprocess
import time
from pathlib import Path
from typing import Optional


SERVICE_TEMPLATE = """[Unit]
Description=ABI Agent: {display_name}
After=network.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
Environment=HERMES_HOME=/home/{username}/.hermes
Environment=PYTHONPATH=/opt/hermes-agent
Environment=HERMES_MEDIA_ALLOW_DIRS=/tmp:/home/{username}/workspace:/home/{username}/.hermes/media
Environment=HERMES_MEDIA_TRUST_RECENT_SECONDS=3600
Environment=ABI_MEMORY_API_URL={memory_api_url}
Environment=ABI_MEMORY_AUTO_EXTRACT_ENABLED=true
Environment=HERMES_KANBAN_HOME={kanban_home}
Environment=API_SERVER_ENABLED=true
Environment=API_SERVER_KEY={api_key}
Environment=API_SERVER_PORT={api_port}
Environment=API_SERVER_HOST={api_host}
WorkingDirectory=/opt/hermes-agent
ExecStart=/opt/hermes-agent/.venv/bin/hermes gateway
Restart=on-failure
RestartSec=10
MemoryMax={memory_limit}

TimeoutStopSec=240s

[Install]
WantedBy=default.target
"""


def _get_uid(username: str) -> Optional[str]:
    result = subprocess.run(["id", "-u", username], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _run_as_user(username: str, cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    uid = _get_uid(username)
    if not uid:
        raise ValueError(f"User {username} not found")
    # Use 'env' wrapper because sudo resets environment variables
    full_cmd = [
        "sudo", "-u", username, "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
    ] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, check=check)


def generate_service(
    username: str,
    display_name: str,
    memory_limit: str = "768M",
    api_port: str = "8400",
    api_key: str = "",
    api_host: str = "127.0.0.1",
    kanban_home: str = "/opt/abi-tools/kanban",
    memory_api_url: str = "http://localhost:8010",
) -> str:
    """Render the user-level systemd unit.

    Workers (every agent except the hub ailean) bind their inter-agent API to
    127.0.0.1; ailean binds 0.0.0.0 because it is the delegation hub. Pass
    api_host="0.0.0.0" only for the hub.
    """
    if not api_key:
        # Defensive: provision() resolves a shared key from existing agents.
        # If empty, generate a random one so the unit is still valid.
        api_key = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    return SERVICE_TEMPLATE.format(
        username=username,
        display_name=display_name,
        memory_limit=memory_limit,
        api_port=api_port,
        api_key=api_key,
        api_host=api_host,
        kanban_home=kanban_home,
        memory_api_url=memory_api_url,
    )


def install_service(username: str, service_content: str) -> str:
    agent_home = Path(f"/home/{username}")
    service_dir = agent_home / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_file = service_dir / "abi-agent.service"
    service_file.write_text(service_content)
    subprocess.run(["chown", "-R", f"{username}:{username}", str(service_dir)], check=True)
    # Enable linger FIRST so the user bus becomes available
    subprocess.run(["loginctl", "enable-linger", username], check=True)
    time.sleep(1)
    _run_as_user(username, ["systemctl", "--user", "daemon-reload"])
    _run_as_user(username, ["systemctl", "--user", "enable", "abi-agent.service"])
    return f"Service installed and enabled for {username}"


def start_service(username: str) -> str:
    try:
        _run_as_user(username, ["systemctl", "--user", "start", "abi-agent.service"])
        return f"Service started for {username}"
    except subprocess.CalledProcessError as e:
        return f"Error starting service: {e.stderr}"


def get_service_status(username: str) -> str:
    try:
        result = _run_as_user(username, ["systemctl", "--user", "status", "abi-agent.service"], check=False)
        return result.stdout or result.stderr
    except Exception as e:
        return f"Error: {e}"
