#!/usr/bin/env python3
# pylint: skip-file
"""test_license_check.py — session-start license check (gateway/license_check.py).

Two layers:
  A/B) Pure classifier tests (_interpret_verify / _interpret_activate) — every
       reason branch deterministically, including edge_block (non-JSON 403 =
       Cloudflare Bot Fight Mode 'error code: 1010'), 404 route drift, 5xx, and
       the not_activated -> activate signal. No network.
  C)   End-to-end async tests — verify_license() over real httpx against the local
       mock Worker (mock_license_worker.py): fresh-bind (1 write), idempotent
       re-verify (NO second write — the hot-path invariant), bound-to-different,
       invalid key, forced verify-mismatch, Forge tier.
  D)   Network failure -> fail-open transient (unreachable host; verify_license
       must not raise).

The hot-path write invariant (the whole point of the fingerprint model): verify
never writes; activate writes exactly once on first bind and is write-free
thereafter — that is what keeps the path under the KV free-tier quota (no CF 1101).

Exit 0 iff all pass. CI-friendly: python3 test_license_check.py
"""
import asyncio
import json
import os
import sys
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)                 # so `gateway.license_check` resolves
sys.path.insert(0, HERE)                 # so `mock_license_worker` resolves
from mock_license_worker import make_server  # noqa: E402
from gateway.license_check import (           # noqa: E402
    verify_license,
    LicenseVerdict,
    _interpret_verify,
    _interpret_activate,
)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    line = f"  [{'PASS' if cond else 'FAIL'}] {name}"
    if not cond and detail:
        line += f"  — {detail}"
    print(line)


def run(coro):
    return asyncio.run(coro)


def admin(port, path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def start_mock():
    httpd, store = make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, store, port


# ============================================================================
print("\n== A. classifier: _interpret_verify ==")
check("A1 200 -> ok + tier",
      _interpret_verify(200, json.dumps({"tier": "Core", "customer": "X"})).tier == "Core")
_v = _interpret_verify(403, json.dumps({"error": "fingerprint_mismatch"}))
check("A2 mismatch -> denied bound_elsewhere + message",
      _v.is_denied and _v.reason == "bound_elsewhere" and bool(_v.user_message))
check("A3 not_activated -> None (activate signal)",
      _interpret_verify(403, json.dumps({"error": "not_activated"})) is None)
_v = _interpret_verify(403, json.dumps({"error": "invalid_key"}))
check("A4 invalid_key -> denied invalid_key", _v.is_denied and _v.reason == "invalid_key")
_v = _interpret_verify(401, json.dumps({"error": "invalid_key"}))
check("A5 401 invalid_key -> denied invalid_key", _v.is_denied and _v.reason == "invalid_key")
_v = _interpret_verify(403, json.dumps({"error": "license_revoked"}))
check("A6 revoked -> denied invalid_key", _v.is_denied and _v.reason == "invalid_key")
_v = _interpret_verify(0, "connection refused")
check("A7 network 0 -> transient fail-open", _v.is_transient and _v.reason == "transient_error")
_v = _interpret_verify(404, json.dumps({"error": "not_found"}))
check("A8 404 -> transient (route drift)", _v.is_transient)
_v = _interpret_verify(503, "overloaded")
check("A9 5xx -> transient", _v.is_transient)
_v = _interpret_verify(403, "<html><head>error code: 1010</head></html>")
check("A10 non-JSON 403 -> edge_block (CF Bot Fight Mode)",
      _v.is_transient and _v.reason == "edge_block")

print("\n== B. classifier: _interpret_activate ==")
_v = _interpret_activate(200, json.dumps({"tier": "Forge", "bound": True}))
check("B1 200 -> ok + tier", _v.ok and _v.tier == "Forge")
_v = _interpret_activate(409, json.dumps({"error": "bound_to_different_machine"}))
check("B2 409 -> denied bound_elsewhere", _v.is_denied and _v.reason == "bound_elsewhere")
_v = _interpret_activate(403, json.dumps({"error": "invalid_key"}))
check("B3 invalid_key -> denied", _v.is_denied and _v.reason == "invalid_key")
_v = _interpret_activate(0, "net err")
check("B4 network -> transient", _v.is_transient)
_v = _interpret_activate(403, "<html>error code: 1010</html>")
check("B5 non-JSON 403 -> edge_block", _v.is_transient and _v.reason == "edge_block")
_v = _interpret_activate(418, json.dumps({"error": "im_a_teapot"}))
check("B6 unclassifiable WRITE -> denied not_activated (fail-closed)",
      _v.is_denied and _v.reason == "not_activated")

# ============================================================================
print("\n== C. end-to-end verify_license() via mock Worker ==")
httpd, store, port = start_mock()
try:
    base = f"http://127.0.0.1:{port}"

    # C1 fresh bind: verify not_activated -> activate binds -> ok + tier. 1 write.
    admin(port, "/__admin/reset")
    v = run(verify_license("fpA", "test-core", base))
    check("C1 fresh-bind -> ok + tier Core", v.ok and v.tier == "Core", f"{v!r}")
    st = admin(port, "/__admin/stats", method="GET")
    check("C1 fresh-bind -> exactly 1 write", st["write_count"] == 1, f"writes={st['write_count']}")

    # C2 idempotent re-verify: NO second write (hot-path invariant).
    v = run(verify_license("fpA", "test-core", base))
    check("C2 re-verify -> ok (idempotent)", v.ok, f"{v!r}")
    st = admin(port, "/__admin/stats", method="GET")
    check("C2 re-verify -> 0 new writes", st["write_count"] == 1, f"writes={st['write_count']}")

    # C3 bound to a DIFFERENT machine (key still bound to fpA, verify fpB).
    v = run(verify_license("fpB", "test-core", base))
    check("C3 different fp -> denied bound_elsewhere", v.is_denied and v.reason == "bound_elsewhere",
          f"{v!r}")
    check("C3 -> user-facing message set", bool(v.user_message))
    st = admin(port, "/__admin/stats", method="GET")
    check("C3 verify mismatch -> NO new writes", st["write_count"] == 1, f"writes={st['write_count']}")

    # C4 invalid key.
    v = run(verify_license("fpA", "definitely-not-a-key", base))
    check("C4 invalid key -> denied invalid_key", v.is_denied and v.reason == "invalid_key", f"{v!r}")

    # C5 forced verify-mismatch path (Worker returns 403 fingerprint_mismatch).
    admin(port, "/__admin/reset")
    admin(port, "/__admin/bind", {"license_key": "test-core", "fp": "fpA"})
    admin(port, "/__admin/verify_mismatch", {"enabled": True})
    v = run(verify_license("fpA", "test-core", base))
    check("C5 forced verify-mismatch -> denied bound_elsewhere",
          v.is_denied and v.reason == "bound_elsewhere", f"{v!r}")
    admin(port, "/__admin/verify_mismatch", {"enabled": False})

    # C6 Forge tier passes through activate.
    admin(port, "/__admin/reset")
    v = run(verify_license("fpF", "test-forge", base))
    check("C6 Forge key -> ok + tier Forge", v.ok and v.tier == "Forge", f"{v!r}")
finally:
    httpd.shutdown()

# ============================================================================
print("\n== D. network failure -> fail-open transient ==")
# Port 1 on loopback: nothing listens -> connection refused (fast). Must not raise.
v = run(verify_license("fpA", "test-core", "http://127.0.0.1:1"))
check("D1 unreachable host -> transient fail-open",
      v.is_transient and v.reason == "transient_error", f"{v!r}")
check("D2 transient -> NO user-facing message (silent fail-open)", not v.user_message)

# ============================================================================
print("")
fails = sum(1 for r in RESULTS if not r)
if fails == 0:
    print(f"ALL PASSED ✅  ({len(RESULTS)} checks)")
    sys.exit(0)
print(f"{fails} FAILURE(S) ❌  ({len(RESULTS)} checks)")
sys.exit(1)
