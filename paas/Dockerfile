# syntax=docker/dockerfile:1
# abi-paas — ABI-managed mini-PaaS (NEW STANDARD SERVICE)
#
# A sibling of abi-agent on opteia-net. The SANDBOXED agent deploys + runs customer
# web apps here through a token-authed management API (:7100, opteia-net ONLY — the
# compose never publishes it to the host). nginx serves the apps to the LAN (:80
# in-container → host ${LAN_IP}:8090, bound LAN-only). Apps run under supervisord;
# each app owns a per-app SQLite in /paas-apps/<app>/data/.
#
# Trust model: this container is the sandbox boundary. The agent's own volume
# (abi-hermes-*: memory/DEK/creds) is NEVER mounted here — a compromised app reaches
# only the LAN/SAP (as the dashboards legitimately do today), NOT the agent's secrets.
# No docker.sock. Apps run as a non-root `app` user; only supervisord/nginx/mgmt API
# run as root.
#
# Build (pilot, on-box):  docker build -t abi-paas:pilot ./paas
# Fleet (Phase 4):        pushed to git.opteia.com/opteia/abi-paas:<tag> via CI.
FROM debian:13-slim

ARG ABI_PAAS_VERSION=pilot
ENV ABI_PAAS_VERSION=${ABI_PAAS_VERSION}

# Runtimes + tooling:
#   nginx          web server / reverse proxy (serves the LAN)
#   nodejs npm     Node apps (+ node-gyp needs python3/make/g++ for native modules)
#   python3-*      Python apps + the mgmt API itself (stdlib only — no pip deps here)
#   supervisor     process manager (keeps app servers alive, restart-on-crash)
#   sqlite3        per-app data layer (better-sqlite3 native build wants libsqlite3-dev)
#   build-essential node-gyp for native npm modules (e.g. better-sqlite3)
#   tini           PID 1 signal handling (clean SIGTERM → supervisord → apps)
#   ca-certificates curl   healthchecks + the provisioner's readiness probe
RUN apt-get update && apt-get install -y --no-install-recommends \
      nginx nodejs npm \
      python3 python3-pip python3-venv \
      supervisor sqlite3 libsqlite3-dev \
      build-essential \
      tini ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root user for app processes (supervisord + nginx + mgmt API stay root).
RUN useradd --system --create-home --uid 1000 app

# Management API (stdlib http.server — zero pip deps in the base layer).
COPY mgmt /opt/paas-mgmt
RUN chmod 0755 /opt/paas-mgmt/api.py

# supervisord main config (owns nginx + mgmt-api + includes app programs).
COPY supervisord.conf /etc/supervisor/supervisord.conf

# nginx config + the per-app include dir.
COPY nginx/nginx.conf /etc/nginx/nginx.conf
RUN mkdir -p /etc/nginx/apps /paas-apps /var/log/supervisor \
    && ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log \
    && touch /var/log/paas-audit.log

# 80  = app traffic (compose publishes ${LAN_IP}:8090:80 — LAN-only)
# 7100 = mgmt API (NOT published — opteia-net peers only, e.g. the agent)
EXPOSE 80 7100
WORKDIR /paas-apps

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
