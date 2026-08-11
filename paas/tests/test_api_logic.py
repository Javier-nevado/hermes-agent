#!/usr/bin/env python3
"""
Logic tests for abi-paas mgmt/api.py — the PURE helpers only (no supervisord/nginx
needed). Guards: source-write path-traversal, port allocation, supervisord/nginx conf
generation (no leftover {{placeholders}}, valid shapes), registry roundtrip, env
sanitization. Run anywhere with just python3:
    python3 tests/test_api_logic.py
"""
import base64
import io
import os
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so `import` finds the mgmt package
os.environ.setdefault("ABI_PAAS_TOKEN", "test-token")

import importlib.util
_spec = importlib.util.spec_from_file_location("paas_api", HERE.parent / "mgmt" / "api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def with_tmp_paths():
    """Point the module's FS constants at a temp tree so conf writers are testable."""
    tmp = Path(tempfile.mkdtemp(prefix="paas-test-"))
    (tmp / "conf.d").mkdir()
    (tmp / "nginx-apps").mkdir()
    (tmp / "apps").mkdir()
    api.CONF_D = tmp / "conf.d"
    api.NGINX_APPS = tmp / "nginx-apps"
    api.APPS_ROOT = tmp / "apps"
    api.REGISTRY = tmp / "apps" / ".registry.json"
    api.AUDIT_LOG = tmp / "audit.log"
    return tmp


# ─── write_source: files ──────────────────────────────────────────────────────
def test_files_write():
    tmp = Path(tempfile.mkdtemp())
    code = tmp / "app"; code.mkdir()
    api.write_source(code, {"type": "files", "files": {
        "server.js": "console.log('hi')",
        "package.json": '{"name":"x"}',
        "lib/util.js": "module.exports={}",
    }})
    check("files: top-level written", (code / "server.js").read_text() == "console.log('hi')")
    check("files: nested written", (code / "lib" / "util.js").exists())


def test_files_traversal_rejected():
    for bad in ("../escape.js", "/etc/x", "a/../../b"):
        tmp = Path(tempfile.mkdtemp()); code = tmp / "app"; code.mkdir()
        try:
            api.write_source(code, {"type": "files", "files": {bad: "x"}})
            check(f"files reject {bad}", False, "did not raise")
        except ValueError:
            check(f"files reject {bad}", True)


# ─── write_source: tar ────────────────────────────────────────────────────────
def _tar(files: dict, malicious: str | None = None) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            ti = tarfile.TarInfo(name); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        if malicious:
            data = b"pwn"; ti = tarfile.TarInfo(malicious); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


def test_tar_extract():
    tmp = Path(tempfile.mkdtemp()); code = tmp / "app"; code.mkdir()
    api.write_source(code, {"type": "tar", "content": _tar({"a.txt": "A", "sub/b.txt": "B"})})
    check("tar: file extracted", (code / "a.txt").read_text() == "A")
    check("tar: nested extracted", (code / "sub" / "b.txt").read_text() == "B")


def test_tar_traversal_rejected():
    tmp = Path(tempfile.mkdtemp()); code = tmp / "app"; code.mkdir()
    try:
        api.write_source(code, {"type": "tar", "content": _tar({"ok.txt": "x"}, malicious="../../../evil.txt")})
        check("tar reject traversal", False, "did not raise")
    except ValueError:
        check("tar reject traversal", True)


# ─── supervisor env sanitization ──────────────────────────────────────────────
def test_supervisor_env():
    app_dir = Path("/paas-apps/demo")
    s = api._supervisor_env({"SAP_USER": 'a"b', "GOOD": "x"}, app_dir, 8123)
    check("env: DATA_DIR present", 'DATA_DIR="/paas-apps/demo/data"' in s, s)
    check("env: APP_DB present", 'APP_DB="/paas-apps/demo/data/app.db"' in s, s)
    check("env: PORT present", 'PORT="8123"' in s, s)
    check("env: quotes stripped from value", 'a"b' not in s and 'SAP_USER="ab"' in s, s)
    check("env: comma-separated, no spaces around ,", ", " not in s, s)
    check("env: ABI_PAAS_TOKEN scrubbed", 'ABI_PAAS_TOKEN=""' in s, s)


# ─── port allocation ──────────────────────────────────────────────────────────
def test_port_alloc():
    reg = {"a": {"port": 8100}, "b": {"port": 8101}}
    check("alloc: first free is 8102", api.allocate_port(reg, None) == 8102)
    check("alloc: requested free ok", api.allocate_port(reg, 8200) == 8200)
    try:
        api.allocate_port(reg, 8100); check("alloc: conflict raises", False)
    except ValueError:
        check("alloc: conflict raises", True)
    try:
        api.allocate_port(reg, 80); check("alloc: out-of-range raises", False)
    except ValueError:
        check("alloc: out-of-range raises", True)


# ─── registry roundtrip ───────────────────────────────────────────────────────
def test_registry():
    with_tmp_paths()
    api.save_registry({"x": {"runtime": "node", "port": 8100}})
    check("registry: roundtrip", api.load_registry() == {"x": {"runtime": "node", "port": 8100}})
    api.REGISTRY.unlink()
    check("registry: missing → {}", api.load_registry() == {})


# ─── conf generation (no leftover placeholders, valid shape) ──────────────────
def test_supervisor_conf():
    with_tmp_paths()
    api.write_supervisor_conf("demo", "python", Path("/paas-apps/demo"),
                              Path("/paas-apps/demo/app"), "python3 server.py", {"K": "v"}, 8123)
    txt = (api.CONF_D / "app-demo.conf").read_text()
    check("sup conf: program header", "[program:app-demo]" in txt)
    check("sup conf: command", "command=python3 server.py" in txt)
    check("sup conf: user=app", f"user={api.APP_USER}" in txt)
    check("sup conf: python venv PATH", ".venv/bin" in txt and "PATH=" in txt)
    check("sup conf: no leftover {{}}", "{{" not in txt and "}}" not in txt)


def test_nginx_conf_dynamic():
    with_tmp_paths()
    api.write_nginx_conf("demo", "node", "/demo/", Path("/paas-apps/demo/app"), 8123)
    txt = (api.NGINX_APPS / "demo.conf").read_text()
    check("nginx dyn: location", "location /demo/ {" in txt)
    check("nginx dyn: proxy_pass port", "http://127.0.0.1:8123/" in txt)
    check("nginx dyn: no leftover {{}}", "{{" not in txt and "}}" not in txt)


def test_nginx_conf_static():
    with_tmp_paths()
    api.write_nginx_conf("report", "static", "/report-q3/", Path("/paas-apps/report/app"), 0)
    txt = (api.NGINX_APPS / "report.conf").read_text()
    check("nginx static: alias", "alias /paas-apps/report/app/;" in txt)
    check("nginx static: no proxy_pass", "proxy_pass" not in txt)
    check("nginx static: no leftover {{}}", "{{" not in txt and "}}" not in txt)


# ─── name/route/env-key validators (shape) ────────────────────────────────────
def test_validators():
    check("name ok", bool(api.NAME_RE.match("sales-dashboard")))
    check("name bad uppercase", not api.NAME_RE.match("Sales"))
    check("route ok", bool(api.ROUTE_RE.match("/sales/")))
    check("route bad no lead slash", not api.ROUTE_RE.match("sales/"))
    check("envkey ok", bool(api.ENVKEY_RE.match("SAP_USER")))
    check("envkey bad lowercase", not api.ENVKEY_RE.match("sap_user"))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n{t.__name__}")
        try:
            t()
        except Exception as e:  # noqa: BLE001
            global FAIL; FAIL += 1
            print(f"  FAIL  raised {type(e).__name__}: {e}")
    print(f"\n{'='*50}\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
