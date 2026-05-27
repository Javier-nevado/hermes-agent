"""HTTP/SSE adapter for stateless Docker MCP servers.

Wraps a docker_base.ABIMCPServer subclass to serve via SSE over HTTP.

Usage:
    python http_adapter.py server.server BrevoMCPServer --port 5109
"""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from mcp.server.sse import SseServerTransport


def create_app(server_instance):
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

    async def health(request: Request):
        return Response(
            json.dumps({"status": "ok", "service": server_instance.SERVICE_NAME}),
            media_type="application/json",
        )

    routes = [
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/health", endpoint=health, methods=["GET"]),
    ]

    return Starlette(routes=routes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("server_module")
    parser.add_argument("server_class")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    port = args.port or int(os.environ.get("MCP_PORT", "5000"))
    module = importlib.import_module(args.server_module)
    server_class = getattr(module, args.server_class)
    server = server_class()

    print(f"Starting {server.SERVICE_NAME} MCP server on :{port} (SSE, stateless)")
    app = create_app(server)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
