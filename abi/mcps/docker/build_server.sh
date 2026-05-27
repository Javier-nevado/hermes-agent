#!/bin/bash
# Build a Docker image for a stateless MCP server.
#
# Usage: build_server.sh <server_name> [tag]
#
# The resulting container is stateless — no credential files mounted.
# Agents pass credentials with each tool call via the 'credentials' parameter.
#

set -euo pipefail

SERVER_NAME="${1:?Usage: build_server.sh <server_name> [tag]}"
TAG="${2:-1.0.0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BUILD_CTX="$SCRIPT_DIR/servers/$SERVER_NAME"
SERVER_SRC="$HERMES_DIR/abi/mcps/servers/$SERVER_NAME"

if [ ! -d "$SERVER_SRC" ]; then
    echo "ERROR: Server source not found at $SERVER_SRC"
    exit 1
fi

IMAGE_NAME="opteia/${SERVER_NAME/_/-}"

MANIFEST="$SERVER_SRC/manifest.json"
if [ -f "$MANIFEST" ]; then
    PORT=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['port'])")
else
    PORT=5000
fi

echo "=== Building $IMAGE_NAME:$TAG (port $PORT, stateless) ==="

# Clean build context
rm -rf "$BUILD_CTX"
mkdir -p "$BUILD_CTX/server"

# Copy server code — strip sys.path, remap imports to docker_base
for pyfile in "$SERVER_SRC"/*.py; do
    [ -f "$pyfile" ] || continue
    basename=$(basename "$pyfile")
    sed -e '/sys\.path\.insert/d' \
        -e 's|from abi\.mcps\.base|from server.docker_base|g' \
        -e 's|from abi\.mcps\.credential_store|from server.docker_base|g' \
        "$pyfile" > "$BUILD_CTX/server/$basename"
done

# Copy stateless base class
cp "$SCRIPT_DIR/docker_base.py" "$BUILD_CTX/server/docker_base.py"

# Copy HTTP adapter
cp "$SCRIPT_DIR/http_adapter.py" "$BUILD_CTX/http_adapter.py"

# Generate requirements.txt
REQ_FILE="$BUILD_CTX/requirements.txt"
cat > "$REQ_FILE" << 'REQS'
mcp>=1.0.0
starlette>=0.27.0
sse-starlette>=1.6.0
uvicorn>=0.23.0
REQS

if [ -f "$SERVER_SRC/requirements.txt" ]; then
    cat "$SERVER_SRC/requirements.txt" >> "$REQ_FILE"
fi

# Detect server class name
CLASS_NAME=$(grep -oP "class \K\w+MCPServer" "$BUILD_CTX/server/server.py" | head -1)
if [ -z "$CLASS_NAME" ]; then
    echo "ERROR: Could not find MCPServer class in server.py"
    exit 1
fi

# Generate Dockerfile — NO volume mounts, stateless
cat > "$BUILD_CTX/Dockerfile" << DOCKERFILE
FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="$SERVER_NAME"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

EXPOSE $PORT
ENV MCP_PORT=$PORT

CMD ["python", "http_adapter.py", "server.server", "$CLASS_NAME", "--port", "$PORT"]
DOCKERFILE

# Build (no cache to ensure latest code)
docker build --no-cache -t "$IMAGE_NAME:$TAG" -t "$IMAGE_NAME:latest" "$BUILD_CTX"

echo ""
echo "=== Built $IMAGE_NAME:$TAG ==="
echo "  Port: $PORT"
echo "  Class: $CLASS_NAME"
echo "  Mode: stateless (credentials passed per-call)"
