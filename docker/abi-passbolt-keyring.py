#!/usr/bin/env python3
"""ABI v4 — stage the agent's Passbolt GPG key into the runtime keyring (boot, root).

Reads the armored ``private_key`` (+ ``public_key``) from the agent's
``passbolt.json`` and imports it into ``$HERMES_HOME/.gnupg`` so the
``passbolt-secure-credentials`` skill (``passbolt_client.py``) can run the
GPGAuth challenge / decrypt flow as the unprivileged ``hermes`` user at runtime.
Sets ultimate ownertrust so ``gpg --decrypt --batch`` never prompts on key trust.

Invoked by ``/etc/cont-init.d/04-passbolt-keyring`` (root, before the gateway
starts). NON-BLOCKING: any failure prints a warning to stderr and exits 0 — a
missing/bad key makes the Passbolt skill fail-soft at runtime, never a dead bot.

The GPG private key IS the bootstrap secret: nothing is logged beyond the
(public) fingerprint, which is printed to stdout for the cont-init banner.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=30)


def main() -> int:
    home = os.environ.get("HERMES_HOME", "/opt/data")
    creds = os.environ.get("PASSBOLT_CREDS_PATH") or os.path.join(
        home, "credentials", "passbolt.json"
    )
    if not os.path.exists(creds):
        return 0  # most agents have no vault — graceful no-op

    try:
        with open(creds, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001 — never fail the boot
        print(f"[passbolt] cont-init: bad creds JSON ({e}) — skipping", file=sys.stderr)
        return 0

    gnupghome = os.path.join(home, ".gnupg")
    os.makedirs(gnupghome, exist_ok=True)
    try:
        os.chmod(gnupghome, 0o700)
    except OSError:
        pass

    def gpg_import(armored: str | None) -> None:
        if not armored or not armored.strip():
            return
        _run(["gpg", "--homedir", gnupghome, "--import", "--batch", "--yes"], stdin=armored)

    # Public first, then private (gpg binds the secret to the matching public).
    gpg_import(data.get("public_key"))
    gpg_import(data.get("private_key"))

    fp = (data.get("key_fingerprint") or data.get("fingerprint") or "").strip()
    if fp:
        # ultimate ownertrust → unattended --decrypt never prompts on key trust
        _run(
            ["gpg", "--homedir", gnupghome, "--import-ownertrust", "--batch"],
            stdin=f"{fp}:6:\n",
        )

    # stdout = fingerprint (for the cont-init banner); warnings already on stderr.
    print(fp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
