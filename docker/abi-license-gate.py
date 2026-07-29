#!/usr/bin/env python3
"""abi-license-gate.py — DIAGNOSTIC ONLY on-box fingerprint license CLI (ABI v4).

STATUS: NOT called from boot anymore. The production license check moved to
SESSION START inside the gateway (gateway/license_check.py → run.py
_check_license), so a licensing failure surfaces a USER-FACING message instead of
silently never starting the gateway. main-wrapper.sh now only STAGES the
fingerprint to ABI_FINGERPRINT (non-blocking); it no longer runs this gate.

This script is retained as a manual OPERATOR tool — run it from the box to force
an activate/verify against api.opteia.com for debugging (confirming a rebind,
inspecting the live fingerprint). It is the original blocking boot gate's logic,
kept for reference + operator use; the equivalent async, fail-open logic lives in
gateway/license_check.py.

The fingerprint model: a license binds to ONE machine fingerprint
(sha256(product_uuid[+MAC]), computed by abi-fingerprint.py). Compute the LIVE
fingerprint → `activate` (binds if unbound, idempotent) → `verify` (read-only;
confirms the binding + returns the tier). Mismatch / invalid → exit non-zero.

NO heartbeat, NO release, NO seat count → NO per-session KV writes (this is what
structurally removes the CF 1101 write-quota outage — see
memory/abi-v4-fingerprint-licensing + memory/api-opteia-license-origin-1101-outage).

Contract the Worker must implement (Phase B; until then tested vs a local mock —
docker/tests/mock_license_worker.py):
  POST {ABI_SEAT_API_URL}/license/activate
       Headers: X-License-Key: <OPTEIA_LICENSE_KEY>
       Body:    {"fingerprint": <fp>}
       200 -> bound to THIS fp (already, or just-bound — idempotent, no re-write)
       409 -> bound to a DIFFERENT fp (needs rebind / support)
       401/403 -> license invalid
  POST /license/verify
       Headers: X-License-Key: <OPTEIA_LICENSE_KEY>
       Body:    {"fingerprint": <fp>}
       200 -> {"tier": "Core"|"Forge"|"Partner", ...}  match -> gateway starts
       403 -> fingerprint mismatch

`activate` MUST be idempotent-read-first (KV.get; only KV.put on first bind) so
the per-boot activate is write-free after the first bind — that is what keeps the
hot path under the free-tier write quota. `verify` is strictly read-only.

Stdlib-only (runs on the bare product python before any venv). The fingerprint is
computed by shelling out to abi-fingerprint.py, which needs root (product_uuid is
mode 0400) — main-wrapper runs this gate as root, pre-setuidgid.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API_BASE = os.environ.get("ABI_SEAT_API_URL", "https://api.opteia.com").rstrip("/")
LICENSE_KEY = os.environ.get("OPTEIA_LICENSE_KEY", "")
# abi-fingerprint.py lives next to this gate in the image (/opt/hermes/docker/).
FINGERPRINT_HELPER = os.environ.get("ABI_FINGERPRINT_HELPER",
                                    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "abi-fingerprint.py"))
PY = os.environ.get("ABI_FINGERPRINT_PY", "/opt/hermes/.venv/bin/python")

# api.opteia.com sits behind Cloudflare Bot Fight Mode, which 403s urllib's default
# "Python-urllib/x.y" UA (edge "error code: 1010"). Every api.opteia.com client must
# send a non-default UA. Env-overridable as a safety valve.
_USER_AGENT = os.environ.get("ABI_SEAT_USER_AGENT", "abi-license-gate/1.0 (+https://opteia.com)")


def _die(msg, rc=1):
    print(f"[license] REFUSED — {msg}", file=sys.stderr)
    sys.exit(rc)


def _fingerprint():
    """Compute the live machine fingerprint via the root helper (abi-fingerprint.py).

    Recomputed from hardware every boot — never read from a file a cloner could
    copy. Tests inject a stub helper via ABI_FINGERPRINT_HELPER (no root needed).
    """
    try:
        out = subprocess.run([PY, FINGERPRINT_HELPER], capture_output=True, text=True, timeout=10)
    except Exception as e:  # helper missing / PY bad
        _die(f"fingerprint helper invocation failed: {e}", rc=2)
    if out.returncode != 0:
        _die(f"fingerprint helper exit {out.returncode}: {out.stderr.strip()[:200]}", rc=2)
    fp = out.stdout.strip()
    if not fp:
        _die("fingerprint helper returned empty", rc=2)
    return fp


def _post(path, payload, timeout=15):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API_BASE + path, data=data, method="POST")
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("X-License-Key", LICENSE_KEY)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # network error / DNS / timeout
        return 0, str(e)


def _edge_error(st, body):
    """A Worker 401/403/409 is JSON {error:...}. A non-JSON 403 (CF Bot Fight Mode
    'error code: 1010' from a banned UA) is a TRANSPORT block at the edge, not a
    license problem — surface it distinctly so it isn't mis-diagnosed."""
    try:
        return json.loads(body).get("error") if body.strip().startswith("{") else None
    except (ValueError, TypeError):
        return None


def verify():
    if not LICENSE_KEY:
        _die("OPTEIA_LICENSE_KEY unset", rc=2)
    fp = _fingerprint()
    print(f"[license] fingerprint = {fp[:12]}… (sha256 of machine hardware)")

    # 1) activate — bind on first boot (idempotent); 409 if bound to a different machine.
    st, body = _post("/license/activate", {"fingerprint": fp})
    if st == 200:
        pass  # bound to this fingerprint (already, or just-bound)
    elif st == 409:
        _die("license is bound to a DIFFERENT machine — contact Opteia support for a rebind")
    elif st in (401, 403):
        err = _edge_error(st, body)
        if err:
            _die(f"license invalid ({err})")
        _die(f"BLOCKED at the edge (HTTP {st}, non-Worker body): {body[:120]!r} — "
             f"check User-Agent/network (CF Bot Fight Mode 403s the default urllib UA)")
    elif st == 404:
        _die("activate route not deployed (license Worker Phase B pending) — refusing to start ungated")
    elif st == 0:
        _die(f"activate network error: {body[:160]}")
    else:
        _die(f"activate unexpected HTTP {st}: {body[:200]}")

    # 2) verify — read-only confirmation of the binding + tier entitlement.
    st, body = _post("/license/verify", {"fingerprint": fp})
    if st == 200:
        try:
            tier = json.loads(body).get("tier", "?")
        except (ValueError, TypeError):
            tier = "?"
        print(f"[license] verified (tier={tier}) — gateway may start")
        return 0
    if st == 403:
        _die("fingerprint mismatch on verify — license bound to another machine; "
             "contact Opteia support for a rebind")
    if st == 404:
        _die("verify route not deployed (license Worker Phase B pending) — refusing to start ungated")
    if st == 401:
        _die(f"verify refused ({_edge_error(st, body) or 'unauthorized'})")
    if st == 0:
        _die(f"verify network error: {body[:160]}")
    _die(f"verify unexpected HTTP {st}: {body[:200]}")
    return 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "verify":
        sys.exit(verify())
    print(f"unknown command: {cmd} (fingerprint model has only 'verify'; "
          f"no checkout/heartbeat/release)", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
