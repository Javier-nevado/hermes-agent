#!/bin/bash
# Build a Docker image for an MCP server.
#
# Usage: build_server.sh <server_name> [tag]
#   server_name: e.g., mcp_brevo, mcp_m365
#   tag: image tag (default: 1.0.0)
#
# Example:
#   build_server.sh mcp_brevo
#   build_server.sh mcp_brevo 1.1.0
#
# This script:
#   1. Creates a build context in abi/mcps/docker/servers/<name>/
#   2. Copies the server code, base class, credential store, and HTTP adapter
#   3. Generates a Dockerfile and requirements.txt
#   4. Builds the Docker image
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

# Get port from manifest or default
MANIFEST="$SERVER_SRC/manifest.json"
if [ -f "$MANIFEST" ]; then
    PORT=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['port'])")
else
    echo "WARNING: No manifest.json found. Using port 5000."
    PORT=5000
fi

echo "=== Building $IMAGE_NAME:$TAG (port $PORT) ==="

# Create build context
mkdir -p "$BUILD_CTX/server"

# Copy server code — strip sys.path manipulations and fix imports
for pyfile in "$SERVER_SRC"/*.py; do
    [ -f "$pyfile" ] || continue
    basename=$(basename "$pyfile")
    # Remove sys.path.insert lines and fix abi.mcps imports
    sed -e '/sys\.path\.insert/d' \
        -e 's|from abi\.mcps\.base|from server.base|g' \
        -e 's|from abi\.mcps\.credential_store|from server.credential_store|g' \
        "$pyfile" > "$BUILD_CTX/server/$basename"
done

# Copy base class
sed -e '/sys\.path\.insert/d' \
    -e 's|from abi\.mcps\.credential_store|from server.credential_store|g' \
    "$HERMES_DIR/abi/mcps/base.py" > "$BUILD_CTX/server/base.py"

# Copy Docker credential store
cp "$SCRIPT_DIR/credential_store.py" "$BUILD_CTX/server/credential_store.py"

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

# Append server-specific requirements if they exist
if [ -f "$SERVER_SRC/requirements.txt" ]; then
    cat "$SERVER_SRC/requirements.txt" >> "$REQ_FILE"
fi

# Detect server class name from server.py
CLASS_NAME=$(grep -oP "class \K\w+MCPServer" "$BUILD_CTX/server/server.py" | head -1)
if [ -z "$CLASS_NAME" ]; then
    echo "ERROR: Could not find MCPServer class in server.py"
    exit 1
fi

# Generate Dockerfile
cat > "$BUILD_CTX/Dockerfile" << DOCKERFILE
FROM python:3.13-alpine
LABEL maintainer="Opteia <abi@opteia.com>"
LABEL org.opteia.mcp="$SERVER_NAME"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY http_adapter.py .

VOLUME /credentials

EXPOSE $PORT
ENV MCP_PORT=$PORT

CMD ["python", "http_adapter.py", "server.server", "$CLASS_NAME", "--port", "$PORT"]
DOCKERFILE

echo "--- Build context ---"
ls -la "$BUILD_CTX/"
echo ""
echo "--- Dockerfile ---"
cat "$BUILD_CTX/Dockerfile"
echo ""

# Build
docker build -t "$IMAGE_NAME:$TAG" -t "$IMAGE_NAME:latest" "$BUILD_CTX"

echo ""
echo "=== Built $IMAGE_NAME:$TAG ==="
echo "  Port: $PORT"
echo "  Class: $CLASS_NAME"
echo "  Start: docker compose -f docker-compose.mcp.yml up -d ${SERVER_NAME/_/-}"
