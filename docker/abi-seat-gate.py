#!/usr/bin/env python3
"""abi-seat-gate.py — on-box seat-licensing client (ABI v4 container deployment).

Enforces per-tier agent-count limits (Core=1, Forge=3, +purchasable add-ons).
The container entrypoint (docker/cont-init.d/03-seat-checkout) checks out a seat
BEFORE the gateway starts: no seat -> exit non-zero -> s6 never brings the
gateway up -> the agent won't run without a licensed seat (compose Restart
retries, but it won't boot until a seat frees). A heartbeat renews the seat;
release frees it on shutdown so crashed/killed agents don't lock seats.

Opt-in via ABI_SEAT_GATE=1. Until the Worker routes (/license/agent-checkout |
agent-heartbeat | agent-release) ship (Phase 0-C), leave ABI_SEAT_GATE unset ->
the agent image runs ungated, exactly as v3.

CONTRACT this client expects the Worker to implement (drives Phase 0-C):
  POST {ABI_SEAT_API_URL}/license/agent-checkout
       Headers: X-License-Key: <OPTEIA_LICENSE_KEY>
       Body:    {"agent_id": <ABI_AGENT_ID>, "version": <ABI_VERSION>}
       200 -> {"seat_token": "<jwt ~10min>", "heartbeat_seconds": <int>}
       402 -> seat limit reached   401/403 -> license invalid
  POST /license/agent-heartbeat   Authorization: Bearer <seat_token>   -> 200
  POST /license/agent-release     Authorization: Bearer <seat_token>   -> 200

Stdlib-only (no deps) so it runs on the bare product python before any venv.
"""
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("ABI_SEAT_API_URL", "https://api.opteia.com").rstrip("/")
LICENSE_KEY = os.environ.get("OPTEIA_LICENSE_KEY", "")
AGENT_ID = os.environ.get("ABI_AGENT_ID", "")
ABI_VERSION = os.environ.get("ABI_VERSION", "")
TOKEN_FILE = os.environ.get("ABI_SEAT_TOKEN_FILE", "/run/abi-seat-token")
CFG_FILE = os.environ.get("ABI_SEAT_CFG_FILE", "/run/abi-seat-cfg")

# api.opteia.com sits behind Cloudflare Bot Fight Mode, which 403s urllib's default
# "Python-urllib/x.y" UA (edge "error code: 1010"). This is the same documented
# requirement as abi/memory/model_cache.py (which even has a test enforcing it) and the
# fleet/capacity reporters — every api.opteia.com client must send a non-default UA. A
# descriptive custom UA passes cleanly; env-overridable as a safety valve if a future CF
# rule ever targets it.
_USER_AGENT = os.environ.get("ABI_SEAT_USER_AGENT", "abi-seat-gate/1.0 (+https://opteia.com)")


def _post(path, payload, headers, timeout=15):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API_BASE + path, data=data, method="POST")
    req.add_header("User-Agent", _USER_AGENT)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # network error / DNS / timeout
        return 0, str(e)


def _read_token():
    try:
        return open(TOKEN_FILE).read().strip()
    except OSError:
        return ""


def checkout():
    if not LICENSE_KEY:
        print("[seat] OPTEIA_LICENSE_KEY unset", file=sys.stderr)
        return 2
    if not AGENT_ID:
        print("[seat] ABI_AGENT_ID unset", file=sys.stderr)
        return 2
    st, body = _post(
        "/license/agent-checkout",
        {"agent_id": AGENT_ID, "version": ABI_VERSION},
        {"X-License-Key": LICENSE_KEY, "Content-Type": "application/json"},
    )
    if st == 200:
        try:
            d = json.loads(body)
            tok = d.get("seat_token", "")
            hb = int(d.get("heartbeat_seconds", 180))
        except (ValueError, TypeError):
            print(f"[seat] checkout 200 but bad body: {body[:200]}", file=sys.stderr)
            return 1
        if not tok:
            print("[seat] checkout 200 but no seat_token", file=sys.stderr)
            return 1
        os.makedirs("/run", exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(tok)
        with open(CFG_FILE, "w") as f:
            f.write(str(hb))
        os.chmod(TOKEN_FILE, 0o600)
        print(f"[seat] seat checked out (heartbeat every {hb}s)")
        return 0
    if st == 402:
        print("[seat] REFUSED — seat limit reached for this license", file=sys.stderr)
        return 1
    if st in (401, 403):
        # A Worker 401/403 is JSON {error:...}. A non-JSON 403 (e.g. Cloudflare Bot Fight
        # Mode "error code: 1010" from a default/banned UA) is a TRANSPORT block at the
        # edge, not a license problem — surface it distinctly so it isn't mis-diagnosed as
        # "license invalid" (which sends the operator chasing the license instead of the UA).
        try:
            err = json.loads(body).get("error") if body.strip().startswith("{") else None
        except (ValueError, TypeError):
            err = None
        if err:
            print(f"[seat] REFUSED — license invalid ({err})", file=sys.stderr)
        else:
            print(f"[seat] BLOCKED at the edge (HTTP {st}, non-Worker body): {body[:120]!r} — "
                  f"check User-Agent/network (CF Bot Fight Mode 403s the default urllib UA)",
                  file=sys.stderr)
        return 1
    print(f"[seat] checkout unexpected HTTP {st}: {body[:200]}", file=sys.stderr)
    return 1


def heartbeat_once():
    tok = _read_token()
    if not tok:
        return False
    st, _ = _post("/license/agent-heartbeat", {}, {"Authorization": f"Bearer {tok}"})
    return st == 200


def release():
    tok = _read_token()
    if not tok:
        return
    _post("/license/agent-release", {}, {"Authorization": f"Bearer {tok}"}, timeout=5)
    try:
        os.unlink(TOKEN_FILE)
    except OSError:
        pass
    print("[seat] seat released")


def heartbeat_loop():
    """Detached loop: renews the seat until SIGTERM (-> release) or seat lost."""
    stopping = {"v": False}

    def term(*_):
        stopping["v"] = True

    signal.signal(signal.SIGTERM, term)
    signal.signal(signal.SIGINT, term)
    try:
        hb = int(open(CFG_FILE).read().strip())
    except (OSError, ValueError):
        hb = 180

    while not stopping["v"]:
        # Sleep in 1s increments so SIGTERM is responsive (not blocked for `hb`).
        for _ in range(hb):
            if stopping["v"]:
                break
            time.sleep(1)
        if stopping["v"]:
            break
        if not heartbeat_once():
            print("[seat] heartbeat FAILED — seat lost/reclaimed; stopping gateway",
                  file=sys.stderr)
            # Take down the supervised services so the agent stops running seatless.
            os.system("s6-svscanctl -t /run/service 2>/dev/null || true")
            return 1
    release()
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "checkout"
    if cmd == "checkout":
        sys.exit(checkout())
    if cmd == "heartbeat":
        sys.exit(heartbeat_loop())
    if cmd == "release":
        release()
        sys.exit(0)
    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
