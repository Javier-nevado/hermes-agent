"""Base MCP server class for ABI credential-free API accessors.

Each MCP server extends this base to get:
- Credential store integration (per-agent encrypted storage)
- Standard auth tools: {service}_status, {service}_login, {service}_logout
- Consistent error handling and JSON responses

Usage:
    class BrevoMCPServer(ABIMCPServer):
        SERVICE_NAME = "brevo"

        def _extra_tools(self):
            return [types.Tool(name="send_email", ...)]

        async def _handle_tool(self, name, args):
            if name == "send_email": ...

    Then: BrevoMCPServer().start()
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add abi/ to path for credential_store import

from server.credential_store import (
    get_credentials,
    save_credentials,
    delete_credentials,
    has_credentials,
)
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


class ABIMCPServer:
    """Base class for ABI MCP servers with credential store integration."""

    SERVICE_NAME: str = ""  # Override in subclass (e.g., "brevo", "m365")

    def __init__(self):
        if not self.SERVICE_NAME:
            raise ValueError("SERVICE_NAME must be set in subclass")
        self.server = Server(f"abi_{self.SERVICE_NAME}")
        self._setup_handlers()

    def _setup_handlers(self):
        """Register list_tools and call_tool handlers."""

        @self.server.list_tools()
        async def list_tools() -> List[types.Tool]:
            tools = [
                types.Tool(
                    name="status",
                    description=f"Check the current authentication status for {self.SERVICE_NAME}.",
                    inputSchema={"type": "object", "properties": {}, "required": []},
                ),
                types.Tool(
                    name="login",
                    description=f"Authenticate with {self.SERVICE_NAME}. Follow the instructions returned.",
                    inputSchema=self._login_schema(),
                ),
                types.Tool(
                    name="logout",
                    description=f"Disconnect {self.SERVICE_NAME} and remove stored credentials.",
                    inputSchema={"type": "object", "properties": {}, "required": []},
                ),
            ]
            tools.extend(self._extra_tools())
            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
            try:
                result = await self._handle_tool(name, arguments or {})
            except Exception as e:
                result = json.dumps({"error": str(e)})
            return [types.TextContent(type="text", text=result)]

    # --- Override points ---

    def _login_schema(self) -> Dict[str, Any]:
        """Override to add login parameters (e.g., api_key, tenant_id)."""
        return {"type": "object", "properties": {}, "required": []}

    def _extra_tools(self) -> List[types.Tool]:
        """Override to register service-specific tools."""
        return []

    async def _handle_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Route tool calls. Override _handle_login and add service handlers."""
        if name == "status":
            return await self._handle_status()
        elif name == "login":
            return await self._handle_login(args)
        elif name == "logout":
            return await self._handle_logout()
        else:
            return await self._handle_service_tool(name, args)

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Override to handle service-specific tools."""
        return json.dumps({"error": f"Unknown tool: {name}"})

    # --- Standard auth handlers ---

    async def _handle_status(self) -> str:
        if not has_credentials(self.SERVICE_NAME):
            return json.dumps({
                "status": "not_configured",
                "message": f"{self.SERVICE_NAME} is not configured. Call {self.SERVICE_NAME}_login to connect.",
            })

        creds = get_credentials(self.SERVICE_NAME)
        if not creds:
            return json.dumps({"status": "error", "message": "Failed to read credentials."})

        validation = await self._validate_credentials(creds)
        if validation and not validation.get("valid"):
            return json.dumps({
                "status": "expired",
                "message": validation.get("message", "Credentials expired or invalid."),
                "action": f"Call {self.SERVICE_NAME}_login to re-authenticate.",
            })

        return json.dumps({
            "status": "connected",
            "message": f"{self.SERVICE_NAME} is connected.",
        })

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        """Override in subclass for service-specific auth flow."""
        return json.dumps({"error": "Login not implemented."})

    async def _handle_logout(self) -> str:
        if delete_credentials(self.SERVICE_NAME):
            return json.dumps({"status": "disconnected", "message": f"{self.SERVICE_NAME} credentials removed."})
        return json.dumps({"status": "not_configured", "message": f"No {self.SERVICE_NAME} credentials found."})

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Override to validate stored credentials against the API. Return None if valid."""
        return None

    # --- Helpers ---

    def _require_creds(self) -> Optional[Dict[str, Any]]:
        """Get credentials or return None."""
        return get_credentials(self.SERVICE_NAME)

    def _save_creds(self, creds: Dict[str, Any]) -> None:
        save_credentials(self.SERVICE_NAME, creds)

    def _no_creds_error(self) -> str:
        return json.dumps({
            "error": f"Not connected. Call {self.SERVICE_NAME}_login first.",
        })

    # --- Run ---

    def start(self):
        """Start the MCP server on stdio (blocking)."""
        asyncio.run(self._run())

    async def _run(self):
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream, write_stream,
                self.server.create_initialization_options(),
            )
