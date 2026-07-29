#!/usr/bin/env python3
# pylint: skip-file
"""test_license_gate.py — Phase A gate test (no Cloudflare, no root).

Runs abi-license-gate.py as a subprocess against the local mock Worker
(mock_license_worker.py) and asserts each contract path:

  1. fresh bind        -> activate binds (1 write) -> verify 200 -> exit 0
  2. idempotent reactivate -> NO second write -> verify 200 -> exit 0   [core invariant]
  3. different machine -> activate 409 -> exit 1  ("DIFFERENT machine")
  4. verify mismatch   -> activate 200, verify 403 -> exit 1 ("mismatch on verify")
  5. invalid key       -> activate 401 -> exit 1 ("license invalid")
  6. missing key env   -> exit 2 ("OPTEIA_LICENSE_KEY unset")
  7. Forge tier passes through verify

Fingerprints are injected via ABI_FINGERPRINT_HELPER=stub_fingerprint.py (no root).
Exit 0 iff all pass; non-zero otherwise (CI-friendly: python3 test_license_gate.py).
"""
import json
import os
import subprocess
import sys
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.normpath(os.path.join(HERE, "..", "abi-license-gate.py"))
STUB = os.path.join(HERE, "stub_fingerprint.py")
sys.path.insert(0, HERE)
from mock_license_worker import make_server  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(cond)
    mark = "PASS" if cond else "FAIL"
    line = f"  [{mark}] {name}"
    if not cond and detail:
        line += f"  — {detail}"
    print(line)


def run_gate(port, *, key="test-core", fp="fpA"):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "ABI_SEAT_API_URL": f"http://127.0.0.1:{port}",
        "ABI_FINGERPRINT_HELPER": STUB,
        "ABI_FINGERPRINT_PY": sys.executable,
        "STUB_FP": fp,
    }
    if key is not None:
        env["OPTEIA_LICENSE_KEY"] = key
    return subprocess.run([sys.executable, GATE, "verify"],
                          env=env, capture_output=True, text=True, timeout=20)


def admin(port, path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def reset(port):
    admin(port, "/__admin/reset")


def stats(port):
    _, body = admin(port, "/__admin/stats", method="GET")
    return body


def main():
    httpd, _store = make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # 1. fresh bind -> activate binds (1 write) -> verify 200 -> exit 0
    reset(port)
    p = run_gate(port, key="test-core", fp="fpA")
    check("1 fresh bind exits 0", p.returncode == 0, f"rc={p.returncode} stderr={p.stderr.strip()[:200]}")
    check("1 verify reports tier=Core", "verified (tier=Core)" in p.stdout, f"stdout={p.stdout.strip()[:200]}")
    st = stats(port)
    check("1 fresh bind = exactly ONE write", st["write_count"] == 1, f"write_count={st['write_count']}")
    check("1 binding stored fpA", st["bindings"].get("test-core") == "fpA", f"bindings={st['bindings']}")

    # 2. idempotent re-activate -> NO second write (the hot-path invariant)
    reset(port)
    p1 = run_gate(port, key="test-core", fp="fpA")
    st1 = stats(port)
    p2 = run_gate(port, key="test-core", fp="fpA")   # same machine, same license
    st2 = stats(port)
    check("2a first activate exit 0", p1.returncode == 0, p1.stderr.strip()[:200])
    check("2b second activate exit 0", p2.returncode == 0, p2.stderr.strip()[:200])
    check("2c re-activate wrote NOTHING extra",
          st1["write_count"] == 1 and st2["write_count"] == 1,
          f"after1={st1['write_count']} after2={st2['write_count']}")

    # 3. different machine -> activate 409 -> exit 1
    reset(port)
    admin(port, "/__admin/bind", {"license_key": "test-core", "fp": "fpA"})
    p = run_gate(port, key="test-core", fp="fpB")     # clone: different fingerprint
    check("3 different machine exits 1", p.returncode == 1, f"rc={p.returncode}")
    check("3 says DIFFERENT machine", "DIFFERENT machine" in p.stderr, f"stderr={p.stderr.strip()[:200]}")
    check("3 no write on rejected activate", stats(port)["write_count"] == 0)

    # 4. verify mismatch (admin-forced) -> activate 200, verify 403 -> exit 1
    reset(port)
    admin(port, "/__admin/verify_mismatch", {"enabled": True})
    p = run_gate(port, key="test-core", fp="fpA")
    check("4 verify mismatch exits 1", p.returncode == 1, f"rc={p.returncode}")
    check("4 says mismatch on verify", "mismatch on verify" in p.stderr, f"stderr={p.stderr.strip()[:200]}")

    # 5. invalid key -> activate 401 -> exit 1
    reset(port)
    p = run_gate(port, key="BAD-KEY", fp="fpA")
    check("5 invalid key exits 1", p.returncode == 1, f"rc={p.returncode}")
    check("5 says license invalid", "license invalid" in p.stderr, f"stderr={p.stderr.strip()[:200]}")

    # 6. missing OPTEIA_LICENSE_KEY -> exit 2 (config error, not license error)
    reset(port)
    p = run_gate(port, key=None, fp="fpA")
    check("6 missing key exits 2", p.returncode == 2, f"rc={p.returncode}")
    check("6 says key unset", "OPTEIA_LICENSE_KEY unset" in p.stderr, f"stderr={p.stderr.strip()[:200]}")

    # 7. Forge tier passes through verify
    reset(port)
    p = run_gate(port, key="test-forge", fp="fpA")
    check("7 Forge verify exit 0", p.returncode == 0, p.stderr.strip()[:200])
    check("7 verify reports tier=Forge", "verified (tier=Forge)" in p.stdout, f"stdout={p.stdout.strip()[:200]}")

    httpd.shutdown()
    passed = sum(RESULTS)
    total = len(RESULTS)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
