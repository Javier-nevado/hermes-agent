#!/usr/bin/env python3
"""mock_license_worker.py — local stand-in for the (Phase B) Cloudflare Worker.

Implements ONLY the fingerprint-licensing contract that docker/abi-license-gate.py
depends on, so Phase A can be tested end-to-end before the real Worker routes ship:

  POST /license/activate   Headers: X-License-Key   Body: {"fingerprint": <fp>}
      200 {"tier", "bound"}            -> bound to THIS fp (just-bound OR already; idempotent)
      409 {"error": "bound_to_different_machine"}
      401 {"error": "invalid_key"}
  POST /license/verify     Headers: X-License-Key   Body: {"fingerprint": <fp>}
      200 {"tier"}                     -> binding matches -> gateway may start
      403 {"error": "fingerprint_mismatch" | "not_activated"}
      401 {"error": "invalid_key"}

PLUS admin hooks so tests can drive the failure branches deterministically (the
real Worker has no such hooks — a clone genuinely produces a 409 via activate):
  POST /__admin/reset                         -> clear all bindings + write counter
  POST /__admin/bind   {"license_key","fp"}   -> pre-set a binding (skip activate)
  POST /__admin/verify_mismatch {"enabled"}   -> force verify to 403 (exercises the
                                                 gate's verify-403 branch, reachable
                                                 on the real Worker only via KV lag)
  GET  /__admin/stats                         -> {"write_count", "bindings"}

INVARIANT the tests assert: a KV "write" (binding put) happens ONLY on first bind.
Re-activate of an already-bound-to-this-fp license and every verify are READ-ONLY —
that is what keeps the hot path under the free-tier write quota (no CF 1101).

Stdlib only. In-process: the test driver calls make_server() and serves it on a
daemon thread; the gate itself runs as a subprocess (to capture real exit codes).
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Store:
    """In-memory KV stand-in. The ONLY field that maps to a real KV.put is
    `bindings` mutation via first-bind; `write_count` tracks exactly that."""

    def __init__(self):
        self.bindings = {}            # license_key -> fingerprint
        self.write_count = 0          # increments ONLY on a first-bind put
        self.verify_mismatch = False  # admin-forced verify 403 (test branch only)
        # key -> tier. Any other key is "invalid" (401).
        self.valid_keys = {"test-core": "Core", "test-forge": "Forge"}


def make_server(port=0):
    """Return (httpd, store). Serve httpd on a daemon thread from the caller."""
    store = Store()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default request logging
            pass

        # --- helpers --------------------------------------------------------
        def _send(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", "0") or "0")
            if n == 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode())
            except (ValueError, UnicodeDecodeError):
                return {}

        def _license_key(self):
            return self.headers.get("X-License-Key", "")

        def _tier(self, key):
            return store.valid_keys.get(key)

        # --- routing --------------------------------------------------------
        def do_GET(self):
            if self.path == "/__admin/stats":
                self._send(200, {"write_count": store.write_count,
                                 "bindings": dict(store.bindings)})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            body = self._read_json()
            if self.path == "/license/activate":
                self._activate(body)
            elif self.path == "/license/verify":
                self._verify(body)
            elif self.path == "/__admin/reset":
                store.bindings.clear()
                store.write_count = 0
                store.verify_mismatch = False
                self._send(200, {"ok": True})
            elif self.path == "/__admin/bind":
                store.bindings[body.get("license_key")] = body.get("fp")
                self._send(200, {"ok": True})
            elif self.path == "/__admin/verify_mismatch":
                store.verify_mismatch = bool(body.get("enabled"))
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not_found"})

        # --- contract -------------------------------------------------------
        def _activate(self, body):
            key = self._license_key()
            tier = self._tier(key)
            if tier is None:
                self._send(401, {"error": "invalid_key"})
                return
            fp = body.get("fingerprint", "")
            cur = store.bindings.get(key)
            if cur is None:                 # first bind -> the ONE write
                store.bindings[key] = fp
                store.write_count += 1
                self._send(200, {"tier": tier, "bound": True})
            elif cur == fp:                 # idempotent re-activate -> NO write
                self._send(200, {"tier": tier, "bound": False})
            else:                           # bound to a different machine
                self._send(409, {"error": "bound_to_different_machine"})

        def _verify(self, body):
            key = self._license_key()
            tier = self._tier(key)
            if tier is None:
                self._send(401, {"error": "invalid_key"})
                return
            if store.verify_mismatch:        # admin-forced (test branch only)
                self._send(403, {"error": "fingerprint_mismatch"})
                return
            fp = body.get("fingerprint", "")
            cur = store.bindings.get(key)
            if cur is None:
                self._send(403, {"error": "not_activated"})
            elif cur == fp:                  # match -> read-only OK
                self._send(200, {"tier": tier})
            else:
                self._send(403, {"error": "fingerprint_mismatch"})

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.store = store
    return httpd, store


if __name__ == "__main__":
    # Run standalone for manual poking: python3 mock_license_worker.py [port]
    import sys
    import threading

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8999
    httpd, store = make_server(port)
    print(f"[mock-license-worker] listening on http://127.0.0.1:{port} "
          f"(valid keys: {list(store.valid_keys)})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
