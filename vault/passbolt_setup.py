#!/usr/bin/env python3
"""Headless Passbolt agent-account setup → passbolt.json.

Given a setup link (from `cake passbolt register_user`), this:
  1. generates the agent's GPG keypair (RSA 3072, no expiry, no passphrase),
  2. completes the Passbolt setup handshake (registers the public key + activates
     the account),
  3. writes passbolt.json (url, user_id, username, key_fingerprint, armored
     public/private keys) to --out.

Protocol (verified against passbolt_api src/Controller/Setup/SetupCompleteController
+ src/Service/Setup/{SetupComplete,AbstractComplete}Service.php):
  - setup link shape: /setup/start/<userId>/<authToken>   (both UUIDs)
  - GET  /setup/start/<userId>/<authToken>.json   → {body.user} (confirms userId)
  - POST /setup/complete/<userId>/<authToken>.json
        body: {"gpgkey": {"armored_key": <PUBLIC>},
               "authenticationtoken": {"token": <authToken>}}
  - Only the PUBLIC key is sent; the private key stays local in passbolt.json
    (the agent needs it to decrypt the GPGAuth challenge each session).

USAGE:
  passbolt_setup.py --url https://passbolt.opteia.com \
                    --setup-link 'https://.../setup/start/<userId>/<token>' \
                    --username ailean@opteia.com --out passbolt.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import requests


def gpg_batch_keygen(home: str, email: str) -> str:
    """Generate an RSA-3072, no-expiry, no-passphrase key; return its fingerprint."""
    batch = f"""%no-protection
Key-Type: RSA
Key-Length: 3072
Key-Usage: sign,encrypt
Name-Real: ABI Agent
Name-Email: {email}
Expire-Date: 0
%commit
"""
    subprocess.run(
        ["gpg", "--homedir", home, "--batch", "--gen-key"],
        input=batch, text=True, capture_output=True, check=True, timeout=90,
    )
    fps = subprocess.run(
        ["gpg", "--homedir", home, "--list-secret-keys", "--with-colons"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    fprs = [ln.split(":")[9] for ln in fps if ln.startswith("fpr:")]
    if not fprs:
        raise RuntimeError("keygen produced no fingerprint")
    return fprs[0]


def gpg_export(home: str, fp: str, secret: bool) -> str:
    cmd = ["gpg", "--homedir", home, "--armor", "--batch", "--yes"]
    cmd += ["--export-secret-keys" if secret else "--export", fp]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    return out.stdout.strip()


def parse_setup_link(setup_link: str) -> tuple[str, str]:
    """Return (userId, authToken) from a /setup/start/<userId>/<token> link.

    Current Passbolt (v4/v5) uses two UUIDs. Older builds used one opaque token
    — for those, userId is resolved from the setup/start response instead.
    """
    m = re.search(r"/setup/start/([0-9a-fA-F-]{36})/([0-9a-fA-F-]{36})", setup_link)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"/setup/start/([A-Za-z0-9]+)", setup_link)
    if m:
        return "", m.group(1)
    raise ValueError(f"could not parse user/token from setup link: {setup_link}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--setup-link", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    user_id, token = parse_setup_link(args.setup_link)

    # ─── 1. keypair ────────────────────────────────────────────────────────
    home = tempfile.mkdtemp(prefix="passbolt-keygen-")
    os.chmod(home, 0o700)
    try:
        fp = gpg_batch_keygen(home, args.username)
        public_key = gpg_export(home, fp, secret=False)
        private_key = gpg_export(home, fp, secret=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    # ─── 2. setup/start (confirm userId) ───────────────────────────────────
    s = requests.Session()
    start_path = f"{user_id}/{token}" if user_id else token
    start = s.get(f"{args.url}/setup/start/{start_path}.json", timeout=20)
    start.raise_for_status()
    body = start.json().get("body", {})
    user_id = user_id or body.get("user", {}).get("id", "")
    if not user_id:
        print("[passbolt_setup] no user_id — parse from setup/start body", file=sys.stderr)
        return 1

    # ─── 3. setup/complete (register public key + activate) ────────────────
    complete_payload = {
        "gpgkey": {"armored_key": public_key},
        "authenticationtoken": {"token": token},
    }
    # Route: POST /setup/complete/{userId}.json  (token travels in the body,
    # NOT the path — see config/routes.php setup scope).
    complete = s.post(
        f"{args.url}/setup/complete/{user_id}.json",
        json=complete_payload, timeout=20,
    )
    if complete.status_code >= 400:
        print(
            f"[passbolt_setup] setup/complete HTTP {complete.status_code}: "
            f"{complete.text[:400]}",
            file=sys.stderr,
        )
        return 1

    # ─── 4. emit passbolt.json (private key = bootstrap secret) ────────────
    creds = {
        "url": args.url,
        "user_id": user_id,
        "username": args.username,
        "key_fingerprint": fp,
        "public_key": public_key,
        "private_key": private_key,
        "setup_token": token,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2)
    os.chmod(args.out, 0o600)
    print(f"[passbolt_setup] wrote {args.out} (fingerprint {fp[:12]}…)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
