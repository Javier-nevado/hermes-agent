"""ABI Agent Deprovisioning.

Removes an agent created by abi-provision: stops/disables the user service,
drops the Linux user (+home), removes the DB role + abi_agents record, and
optionally closes the Telegram forum topic. Idempotent — safe to re-run.

Usage:
    sudo python3 scripts/abi-deprovision --name mga
    sudo python3 scripts/abi-deprovision --name qa --keep-user            # keep user/home
    sudo python3 scripts/abi-deprovision --name mga --topic-id 1234 --admin-bot-token <tok>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from abi.provision.agent import run, _psql, TG_TEAM_GROUP
from abi.provision.telegram import close_forum_topic


def _uid(name: str):
    res = run(["id", "-u", name], check=False)
    return res.stdout.strip() if res.returncode == 0 else None


def _run_as_user(name: str, cmd: list):
    uid = _uid(name)
    if not uid:
        return
    full = ["sudo", "-u", name, "env",
            f"XDG_RUNTIME_DIR=/run/user/{uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus"] + cmd
    run(full, check=False)


def stop_service(name: str) -> None:
    if not _uid(name):
        print(f"  User {name} not found, skipping service teardown.")
        return
    _run_as_user(name, ["systemctl", "--user", "stop", "abi-agent.service"])
    _run_as_user(name, ["systemctl", "--user", "disable", "abi-agent.service"])
    _run_as_user(name, ["systemctl", "--user", "reset-failed", "abi-agent.service"])
    unit = Path(f"/home/{name}/.config/systemd/user/abi-agent.service")
    if unit.exists():
        run(["sudo", "rm", "-f", str(unit)], check=False)
    # Tear down the user manager that enable-linger started (user@UID), else
    # userdel refuses to remove a user with live processes.
    run(["sudo", "loginctl", "disable-linger", name], check=False)
    uid = _uid(name)
    if uid:
        run(["sudo", "systemctl", "stop", f"user@{uid}.service"], check=False)
    print(f"  Service stopped + disabled + unit removed for {name}")


def remove_db(name: str) -> None:
    res = _psql(f"DELETE FROM abi_agents WHERE username = '{name}';")
    print(f"  abi_agents record: {'deleted' if res.returncode == 0 else 'none/failed'}")
    res = _psql(f"DROP ROLE IF EXISTS {name};")
    print(f"  DB role: {'dropped' if res.returncode == 0 else 'skipped/failed'}")


def remove_user(name: str, keep: bool) -> None:
    if keep:
        print(f"  Keeping user + home (--keep-user).")
        return
    if not _uid(name):
        print(f"  User {name} already gone.")
        return
    # Kill any lingering processes owned by the user, then remove.
    run(["sudo", "killall", "-9", "-u", name], check=False)
    res = run(["sudo", "userdel", "-r", name], check=False)
    if res.returncode == 0:
        print(f"  Removed user {name} + home /home/{name}")
        return
    # -r can fail on root-owned files in home (e.g. SOUL.service.md); force it.
    res2 = run(["sudo", "userdel", name], check=False)
    run(["sudo", "rm", "-rf", f"/home/{name}"], check=False)
    if res2.returncode == 0:
        print(f"  Removed user {name} (home force-removed)")
    else:
        print(f"  userdel failed: {(res.stderr or res2.stderr or '').strip()[:200]}")


def deprovision(name: str, keep_user: bool = False,
                topic_id: int = None, admin_bot_token: str = None) -> None:
    print(f"\nDeprovisioning agent: {name}")
    stop_service(name)
    remove_db(name)
    if topic_id and admin_bot_token:
        if close_forum_topic(admin_bot_token, TG_TEAM_GROUP, topic_id):
            print(f"  Closed TG topic {topic_id}")
        else:
            print(f"  Could not close TG topic {topic_id} (token/perms?)")
    remove_user(name, keep_user)
    print(f"Done. '{name}' deprovisioned.")


def main():
    p = argparse.ArgumentParser(description="Deprovision an ABI agent")
    p.add_argument("--name", required=True, help="Agent Linux username")
    p.add_argument("--keep-user", action="store_true", help="Keep the Linux user + home")
    p.add_argument("--topic-id", type=int, help="TG forum topic ID to close")
    p.add_argument("--admin-bot-token", help="Admin bot token to close the TG topic")
    args = p.parse_args()
    deprovision(args.name, args.keep_user, args.topic_id, args.admin_bot_token)


if __name__ == "__main__":
    main()
