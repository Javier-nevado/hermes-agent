"""HTTP/SSE adapter for ABI MCP servers.

Wraps any ABIMCPServer subclass to serve via SSE over HTTP,
enabling Docker container deployment.

Usage:
    python http_adapter.py <server_module> <server_class> [port]

    # Example:
    python http_adapter.py abi.mcps.servers.mcp_brevo.server BrevoMCPServer 5109

Environment:
    MCP_PORT       — Port to listen on (default: from argv or 5000)
    CREDENTIALS_PATH — Mount point for credentials (default: auto-detect from HERMES_HOME)
    HERMES_HOME    — Agent home directory (set by Hermes or Docker env)
"""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

# Ensure abi/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from mcp.server.sse import SseServerTransport


def create_app(server_instance):
    """Create a Starlette ASGI app wrapping an ABIMCPServer for SSE transport."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server_instance.server.run(
                streams[0],
                streams[1],
                server_instance.server.create_initialization_options(),
            )
        return Response()

    routes = [
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ]

    # Health check endpoint
    async def health(request: Request):
        return Response(
            json.dumps({"status": "ok", "service": server_instance.SERVICE_NAME}),
            media_type="application/json",
        )
    routes.append(Route("/health", endpoint=health, methods=["GET"]))

    return Starlette(routes=routes)


def main():
    parser = argparse.ArgumentParser(description="HTTP/SSE adapter for ABI MCP servers")
    parser.add_argument("server_module", help="Python module path (e.g., abi.mcps.servers.mcp_brevo.server)")
    parser.add_argument("server_class", help="Server class name (e.g., BrevoMCPServer)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    args = parser.parse_args()

    # Determine port
    port = args.port or int(os.environ.get("MCP_PORT", "5000"))

    # Import and instantiate the server
    module = importlib.import_module(args.server_module)
    server_class = getattr(module, args.server_class)
    server = server_class()

    print(f"Starting {server.SERVICE_NAME} MCP server on :{port} (SSE transport)")
    app = create_app(server)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
