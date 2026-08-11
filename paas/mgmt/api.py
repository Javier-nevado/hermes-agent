#!/usr/bin/env python3
"""
abi-paas management API — the agent's control surface (stdlib only, zero pip deps).

Runs in the abi-paas container on :7100, reachable only on opteia-net (the compose
never publishes :7100 to the host). The sandboxed agent authenticates with a shared
bearer token (ABI_PAAS_TOKEN) and deploys/runs/stops customer web apps here. The
agent's own volume (abi-hermes-*) is NEVER mounted in this container — isolation is
the container boundary; a compromised app reaches only the LAN/SAP, not agent secrets.

Deploy pipeline (POST /apps/{name}):
    validate → write source → install deps → write supervisord program →
    supervisorctl reread+update+start → write nginx location → nginx -t → reload →
    audit. nginx is tested BEFORE reload so a bad conf can't take the server down;
    on failure the half-written confs are rolled back.

Each app lives in /paas-apps/<name>/ :
    app/    code (overwritten on redeploy)
    data/   PERSISTENT — per-app SQLite (app.db) survives redeploy. Never wiped.
    logs/   stdout/stderr captured by supervisord
    .venv/  (python only) per-app virtualenv
Apps run as the non-root `app` user (uid 1000); only supervisord/nginx/mgmt API run
as root. The mgmt API chowns each app dir to app:app before running installs.
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─── paths + config ──────────────────────────────────────────────────────────
APPS_ROOT = Path("/paas-apps")
CONF_D = Path("/etc/supervisor/conf.d")        # app-<name>.conf (supervisord programs)
NGINX_APPS = Path("/etc/nginx/apps")           # <name>.conf (nginx location blocks)
REGISTRY = APPS_ROOT / ".registry.json"        # app name → {runtime, route, port, ...}
AUDIT_LOG = Path("/var/log/paas-audit.log")

APP_USER = os.environ.get("ABI_PAAS_APP_USER", "app")
PORT_LO, PORT_HI = 8100, 8999                   # internal app ports; nginx proxies to 127.0.0.1:<port>

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ROUTE_RE = re.compile(r"^/[a-z0-9][a-z0-9/_-]*$")
ENVKEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
RELPATH_RE = re.compile(r"^[A-Za-z0-9_./@-]+$")

TOKEN = os.environ.get("ABI_PAAS_TOKEN", "")
LISTEN_HOST = os.environ.get("ABI_PAAS_LISTEN", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("ABI_PAAS_PORT", "7100"))
MAX_BODY = 100 * 1024 * 1024                    # 100MB deploy cap

MUTEX = threading.Lock()                        # serialize mutating deploys (shared registry/confs)


# ─── small helpers ───────────────────────────────────────────────────────────
def audit(msg: str) -> None:
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def run(cmd: list[str], **kw) -> tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr). Never raises."""
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, p.stdout or "", p.stderr or ""


def supervisorctl(*args: str) -> tuple[int, str, str]:
    return run(["supervisorctl"] + list(args))


def nginx_test_reload() -> tuple[bool, str]:
    rc, out, err = run(["nginx", "-t"])
    if rc != 0:
        return False, (err or out).strip()
    rc, out, err = run(["nginx", "-s", "reload"])
    if rc != 0:
        return False, (err or out).strip()
    return True, "reloaded"


# ─── registry ────────────────────────────────────────────────────────────────
def load_registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(reg: dict) -> None:
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.replace(REGISTRY)


def used_ports(reg: dict) -> set[int]:
    return {int(v.get("port", 0)) for v in reg.values() if v.get("port")}


def allocate_port(reg: dict, requested: int | None = None) -> int:
    in_use = used_ports(reg)
    if requested:
        if not (PORT_LO <= requested <= PORT_HI):
            raise ValueError(f"port must be in [{PORT_LO},{PORT_HI}]")
        if requested in in_use:
            raise ValueError(f"port {requested} already in use")
        return requested
    for p in range(PORT_LO, PORT_HI + 1):
        if p not in in_use:
            return p
    raise ValueError("no free app ports")


# ─── source write ────────────────────────────────────────────────────────────
def _safe_member(base: Path, member: tarfile.TarInfo) -> Path:
    target = (base / member.name).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise ValueError(f"unsafe tar path: {member.name}")
    return target


def write_source(code_dir: Path, source: dict) -> None:
    stype = (source or {}).get("type")
    if stype == "files":
        files = source.get("files") or source.get("content") or {}
        if not isinstance(files, dict):
            raise ValueError("source.files must be an object of {path: content}")
        for rel, content in files.items():
            if rel.startswith("/") or ".." in Path(rel).parts or not RELPATH_RE.match(rel):
                raise ValueError(f"unsafe file path: {rel}")
            dest = code_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
    elif stype == "tar":
        raw = source.get("content") or source.get("data")
        if not raw:
            raise ValueError("source.content (base64 tar) required for type=tar")
        blob = base64.b64decode(raw)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
            for m in tf.getmembers():
                _safe_member(code_dir, m)      # raises on traversal
            tf.extractall(code_dir)
    else:
        raise ValueError("source.type must be 'files' or 'tar'")


# ─── deps install (as the non-root app user) ─────────────────────────────────
def install_deps(runtime: str, app_dir: Path, code_dir: Path) -> None:
    if runtime == "node":
        if (code_dir / "package.json").exists():
            rc, out, err = run(["runuser", "-u", APP_USER, "--", "npm", "install",
                                "--omit=dev", "--no-audit", "--no-fund"], cwd=code_dir)
            if rc != 0:
                raise RuntimeError(f"npm install failed: {(err or out).strip()[-800:]}")
    elif runtime == "python":
        venv = app_dir / ".venv"
        if not venv.exists():
            rc, out, err = run(["runuser", "-u", APP_USER, "--", "python3", "-m", "venv", str(venv)])
            if rc != 0:
                raise RuntimeError(f"venv create failed: {(err or out).strip()[-800:]}")
        reqs = code_dir / "requirements.txt"
        if reqs.exists():
            rc, out, err = run(["runuser", "-u", APP_USER, "--", str(venv / "bin" / "pip"),
                                "install", "-r", str(reqs)])
            if rc != 0:
                raise RuntimeError(f"pip install failed: {(err or out).strip()[-800:]}")


# ─── conf writers ────────────────────────────────────────────────────────────
# supervisord environment= line: KEY="val",... (quotes/newlines stripped from vals)
def _supervisor_env(extra: dict, app_dir: Path, port: int) -> str:
    base = {
        "DATA_DIR": str(app_dir / "data"),
        "APP_DB": str(app_dir / "data" / "app.db"),
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
        "NODE_ENV": "production",
        # SCRUB the inherited mgmt token so a deployed app can't drive the API itself
        # (supervisord's environment= overrides the inherited container env per-program).
        "ABI_PAAS_TOKEN": "",
    }
    base.update({k: str(v) for k, v in extra.items()})
    parts = []
    for k, v in base.items():
        v = str(v).replace('"', "").replace("\n", " ").replace("\r", " ")
        parts.append(f'{k}="{v}"')
    return ",".join(parts)


def write_supervisor_conf(name: str, runtime: str, app_dir: Path,
                          code_dir: Path, start: str, env: dict, port: int) -> None:
    if runtime == "python":
        # put the per-app venv first on PATH so `python3` resolves to it
        path_env = f'PATH="{app_dir / ".venv" / "bin"}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"'
        environment = _supervisor_env(env, app_dir, port) + "," + path_env
    else:
        environment = _supervisor_env(env, app_dir, port)
    conf = (
        f"[program:app-{name}]\n"
        f"directory={code_dir}\n"
        f"command={start}\n"
        f"user={APP_USER}\n"
        f"autostart=true\n"
        f"autorestart=true\n"
        f"startsecs=3\n"
        f"stopasgroup=true\n"
        f"killasgroup=true\n"
        f"stdout_logfile={app_dir / 'logs' / 'stdout.log'}\n"
        f"stdout_logfile_maxbytes=2MB\n"
        f"stdout_logfile_backups=3\n"
        f"stderr_logfile={app_dir / 'logs' / 'stderr.log'}\n"
        f"stderr_logfile_maxbytes=2MB\n"
        f"stderr_logfile_backups=3\n"
        f"environment={environment}\n"
    )
    (CONF_D / f"app-{name}.conf").write_text(conf)


# nginx templates ({{PLACEHOLDER}} + .replace → no f-string brace clashes)
_NGINX_PROXY = (
    "location {{ROUTE}} {\n"
    "    proxy_pass http://127.0.0.1:{{PORT}}/;\n"
    "    proxy_set_header Host $host;\n"
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "    proxy_set_header X-Forwarded-Proto $scheme;\n"
    "    proxy_read_timeout 60s;\n"
    "    proxy_connect_timeout 10s;\n"
    "}\n"
)
_NGINX_STATIC = (
    "location {{ROUTE}} {\n"
    "    alias {{CODE}}/;\n"
    "    autoindex on;\n"
    "    index index.html;\n"
    "}\n"
)


def write_nginx_conf(name: str, runtime: str, route: str, code_dir: Path, port: int) -> None:
    if runtime == "static":
        block = _NGINX_STATIC.replace("{{ROUTE}}", route).replace("{{CODE}}", str(code_dir))
    else:
        block = _NGINX_PROXY.replace("{{ROUTE}}", route).replace("{{PORT}}", str(port))
    (NGINX_APPS / f"{name}.conf").write_text(block)


def remove_confs(name: str) -> None:
    for p in (CONF_D / f"app-{name}.conf", NGINX_APPS / f"{name}.conf"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ─── deploy / remove ─────────────────────────────────────────────────────────
def deploy_app(name: str, spec: dict) -> dict:
    runtime = spec.get("runtime")
    route = (spec.get("route") or f"/{name}/").rstrip("/") + "/"
    start = spec.get("start")
    env = spec.get("env") or {}
    source = spec.get("source") or {}

    if runtime not in ("node", "python", "static"):
        raise ValueError("runtime must be node|python|static")
    if not ROUTE_RE.match(route):
        raise ValueError(f"invalid route: {route}")
    if runtime in ("node", "python") and not start:
        raise ValueError(f"'start' command required for runtime={runtime}")
    if not isinstance(env, dict) or any(not ENVKEY_RE.match(k) for k in env):
        raise ValueError("env keys must be uppercase [A-Z_][A-Z0-9_]*")

    reg = load_registry()
    port = allocate_port(reg, spec.get("port"))

    app_dir = APPS_ROOT / name
    code_dir = app_dir / "app"
    data_dir = app_dir / "data"
    log_dir = app_dir / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    # overwrite code on redeploy, PRESERVE data/
    if code_dir.exists():
        shutil.rmtree(code_dir)
    code_dir.mkdir(parents=True, exist_ok=True)

    write_source(code_dir, source)
    # chown the whole app tree to the app user so installs + the process can write
    run(["chown", "-R", f"{APP_USER}:{APP_USER}", str(app_dir)])
    install_deps(runtime, app_dir, code_dir)

    if runtime in ("node", "python"):
        write_supervisor_conf(name, runtime, app_dir, code_dir, start, env, port)
        for step in ("reread", "update"):
            rc, out, err = supervisorctl(step)
            if rc != 0 and "no action" not in (out + err).lower() and "added" not in out:
                # reread/update report changes; non-zero with real error → roll back
                audit(f"deploy {name}: supervisorctl {step} rc={rc} err={err.strip()[:200]}")
        rc, out, err = supervisorctl("restart", f"app-{name}")
        if rc != 0:
            remove_confs(name)
            supervisorctl("reread"); supervisorctl("update")
            nginx_test_reload()
            raise RuntimeError(f"app failed to start: {(err or out).strip()[-800:]}")

    write_nginx_conf(name, runtime, route, code_dir, port)
    ok, msg = nginx_test_reload()
    if not ok:
        # nginx rejected the new route conf — roll back the route, keep the app running
        try:
            (NGINX_APPS / f"{name}.conf").unlink()
        except FileNotFoundError:
            pass
        nginx_test_reload()
        raise RuntimeError(f"nginx reload failed (route not applied): {msg}")

    reg[name] = {
        "runtime": runtime,
        "route": route,
        "port": port if runtime != "static" else None,
        "start": start,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_registry(reg)
    audit(f"deploy name={name} runtime={runtime} route={route} port={port}")
    return reg[name]


def remove_app(name: str) -> dict:
    reg = load_registry()
    if name not in reg:
        raise KeyError(name)
    supervisorctl("stop", f"app-{name}")
    remove_confs(name)
    supervisorctl("reread"); supervisorctl("update")
    nginx_test_reload()
    # keep data/ (PERSISTENT) on remove; only wipe code + logs + venv
    app_dir = APPS_ROOT / name
    for sub in ("app", "logs", ".venv"):
        p = app_dir / sub
        if p.exists():
            shutil.rmtree(p)
    del reg[name]
    save_registry(reg)
    audit(f"remove name={name} (data/ preserved)")
    return {"removed": name, "data_preserved": str(app_dir / "data")}


def app_status(name: str) -> dict:
    rc, out, _ = supervisorctl("status", f"app-{name}")
    return {"name": name, "supervisor": out.strip(), "running": "RUNNING" in out}


# ─── HTTP handler ────────────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


class Handler(BaseHTTPRequestHandler):
    server_version = "abi-paas/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence default stderr noise
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ── auth ──
    def _check_token(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:].strip(), TOKEN)

    # ── io ──
    def _send(self, code: int, obj: dict | str):
        body = obj if isinstance(obj, str) else json.dumps(obj)
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json" if isinstance(obj, dict) else "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            raise ApiError(413, f"body too large (>{MAX_BODY} bytes)")
        if length == 0:
            return {}
        raw = self._rfile_read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ApiError(400, f"invalid JSON: {e}")

    def _rfile_read(self, length: int) -> bytes:
        return self.rfile.read(length)

    # ── routing ──
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        try:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            # /health is the only unauth endpoint (opteia-net only; no host port)
            if path == "/health":
                return self._send(200, {"ok": True, "service": "abi-paas",
                                        "version": os.environ.get("ABI_PAAS_VERSION", "pilot")})
            if not TOKEN:
                return self._send(503, {"error": "ABI_PAAS_TOKEN not set on the server"})
            if not self._check_token():
                return self._send(401, {"error": "unauthorized"})

            if path == "/apps":
                if method != "GET":
                    raise ApiError(405, "use GET /apps")
                return self._send(200, load_registry())

            parts = path.strip("/").split("/")
            # /apps/{name}[/logs|/{action}]
            if len(parts) == 2 and parts[0] == "apps":
                name = parts[1]
                if not NAME_RE.match(name):
                    raise ApiError(400, "bad app name")
                if method == "GET":
                    reg = load_registry()
                    if name not in reg:
                        raise ApiError(404, "no such app")
                    return self._send(200, {**reg[name], **app_status(name)})
                if method == "DELETE":
                    with MUTEX:
                        return self._send(200, remove_app(name))
                if method == "POST":  # deploy
                    spec = self._read_body()
                    with MUTEX:
                        return self._send(200, deploy_app(name, spec))
                raise ApiError(405, "method not allowed")

            if len(parts) == 3 and parts[0] == "apps":
                name, action = parts[1], parts[2]
                if not NAME_RE.match(name):
                    raise ApiError(400, "bad app name")
                if method != "POST":
                    raise ApiError(405, "use POST")
                if action in ("start", "stop", "restart"):
                    rc, out, err = supervisorctl(action, f"app-{name}")
                    if rc != 0:
                        raise ApiError(500, f"{action} failed: {(err or out).strip()[-400:]}")
                    audit(f"action name={name} action={action}")
                    return self._send(200, {**app_status(name)})
                if action == "logs":
                    return self._send(200, _tail_logs(name))
                raise ApiError(400, "unknown action")

            if path == "/routes/reload":
                if method != "POST":
                    raise ApiError(405, "use POST")
                ok, msg = nginx_test_reload()
                return self._send(200 if ok else 500, {"ok": ok, "msg": msg})

            raise ApiError(404, f"no route for {method} {path}")
        except ApiError as e:
            return self._send(e.code, {"error": e.msg})
        except (ValueError, KeyError) as e:
            return self._send(400 if isinstance(e, ValueError) else 404, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — surface deploy failures cleanly
            audit(f"ERROR {method} {self.path}: {e}")
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def _tail_logs(name: str, n: int = 200) -> dict:
    out = {"stdout": "", "stderr": ""}
    for key, fname in (("stdout", "stdout.log"), ("stderr", "stderr.log")):
        p = APPS_ROOT / name / "logs" / fname
        try:
            lines = p.read_text(errors="replace").splitlines()[-n:]
            out[key] = "\n".join(lines)
        except OSError:
            out[key] = ""
    return out


def main() -> None:
    if not TOKEN:
        sys.stderr.write("FATAL: ABI_PAAS_TOKEN not set — refusing to start the mgmt API.\n")
        sys.exit(2)
    CONF_D.mkdir(parents=True, exist_ok=True)
    NGINX_APPS.mkdir(parents=True, exist_ok=True)
    APPS_ROOT.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    sys.stderr.write(f"abi-paas mgmt API listening on {LISTEN_HOST}:{LISTEN_PORT}\n")
    audit("mgmt-api started")
    srv.serve_forever()


if __name__ == "__main__":
    main()
