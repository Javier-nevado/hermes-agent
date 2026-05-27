"""Stateless MCP server base for Docker containers.

Credentials are NOT stored server-side. The agent passes them with each
tool call. Login validates and returns credentials for the agent to keep.

Usage: Same as ABIMCPServer — subclass and override _extra_tools, _handle_service_tool, etc.

Differences from stdio ABIMCPServer:
  - _require_creds() reads from tool args (via contextvars), not files
  - login returns credentials instead of saving to disk
  - logout is a no-op (stateless)
  - credentials parameter is auto-injected into every tool schema
"""

import asyncio
import json
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# Per-request credentials — set by call_tool handler, read by _require_creds
_request_creds: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "request_creds", default=None
)

# Re-export the name so subclasses don't need changes
# (build_server.sh changes: from server.base import ABIMCPServer
#                           → from server.docker_base import ABIMCPServer)


class ABIMCPServer:
    """Stateless MCP server base. Credentials passed per tool call."""

    SERVICE_NAME: str = ""

    def __init__(self):
        if not self.SERVICE_NAME:
            raise ValueError("SERVICE_NAME must be set in subclass")
        self.server = Server(f"abi_{self.SERVICE_NAME}")
        self._setup_handlers()

    def _setup_handlers(self):
        @self.server.list_tools()
        async def list_tools() -> List[types.Tool]:
            tools = [
                types.Tool(
                    name="status",
                    description=f"Check {self.SERVICE_NAME} status.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                types.Tool(
                    name="login",
                    description=f"Authenticate with {self.SERVICE_NAME}. "
                    f"Returns credentials for you to store and pass with each call.",
                    inputSchema=self._login_schema(),
                ),
                types.Tool(
                    name="logout",
                    description=f"Clear your stored {self.SERVICE_NAME} credentials (client-side).",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]
            service_tools = self._extra_tools()
            # Auto-inject credentials parameter into every service tool
            for tool in service_tools:
                schema = tool.inputSchema
                if schema.get("type") == "object":
                    props = schema.setdefault("properties", {})
                    props["credentials"] = {
                        "type": "object",
                        "description": f"Service credentials (from {self.SERVICE_NAME}_login). "
                        "Pass the full object returned by login.",
                    }
            tools.extend(service_tools)
            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]):
            args = arguments or {}
            # Extract credentials from args, set in context for _require_creds
            creds = args.pop("credentials", None)
            token = _request_creds.set(creds)
            try:
                result = await self._handle_tool(name, args)
            except Exception as e:
                result = json.dumps({"error": str(e)})
            finally:
                _request_creds.reset(token)
            return [types.TextContent(type="text", text=result)]

    # --- Override points (same API as stdio base) ---

    def _login_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _extra_tools(self) -> List[types.Tool]:
        return []

    async def _handle_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "status":
            return await self._handle_status()
        elif name == "login":
            return await self._handle_login(args)
        elif name == "logout":
            return await self._handle_logout()
        else:
            return await self._handle_service_tool(name, args)

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        return json.dumps({"error": f"Unknown tool: {name}"})

    # --- Auth handlers (stateless) ---

    async def _handle_status(self) -> str:
        return json.dumps({
            "status": "ready",
            "message": f"Stateless {self.SERVICE_NAME} server. "
            "Pass credentials with each tool call.",
        })

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        """Validate credentials and return them to the agent."""
        validation = await self._validate_credentials(args)
        if validation and not validation.get("valid"):
            return json.dumps({"error": validation.get("message", "Validation failed.")})
        return json.dumps({
            "status": "authenticated",
            "credentials": args,
            "message": f"{self.SERVICE_NAME} authenticated. "
            "Store these credentials and pass them with each tool call.",
        })

    async def _handle_logout(self) -> str:
        return json.dumps({
            "status": "ok",
            "message": f"Stateless server — delete your stored {self.SERVICE_NAME} credentials.",
        })

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Override to validate credentials. Return None if valid."""
        return None

    # --- Helpers (same API as stdio base) ---

    def _require_creds(self) -> Optional[Dict[str, Any]]:
        """Get credentials from the current request context."""
        return _request_creds.get()

    def _save_creds(self, creds: Dict[str, Any]) -> None:
        pass  # No-op: stateless

    def _no_creds_error(self) -> str:
        return json.dumps({
            "error": f"No credentials provided. "
            f"Call {self.SERVICE_NAME}_login first, then pass credentials with each call.",
        })

    # --- Run ---

    def start(self):
        asyncio.run(self._run())

    async def _run(self):
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream, write_stream,
                self.server.create_initialization_options(),
            )
