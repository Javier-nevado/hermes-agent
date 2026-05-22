"""Brevo MCP Server — Email API accessor.

Tools:
    brevo_status   — Check auth status
    brevo_login    — Store API key (agent asks owner for key)
    brevo_logout   — Remove credentials
    brevo_send     — Send an email
    brevo_events   — Get recent email events (opens, clicks, etc.)
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from abi.mcps.base import ABIMCPServer
from mcp import types

# Brevo API constants
BREVO_API_BASE = "https://api.brevo.com/v3"


class BrevoMCPServer(ABIMCPServer):
    SERVICE_NAME = "brevo"

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "Brevo API key (starts with xkeysib-). Find at settings.brevo.com/keys",
                },
            },
            "required": ["api_key"],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            types.Tool(
                name="send",
                description="Send an email via Brevo.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address.",
                        },
                        "to_name": {
                            "type": "string",
                            "description": "Recipient name (optional).",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject.",
                        },
                        "html_body": {
                            "type": "string",
                            "description": "HTML body content.",
                        },
                        "from_email": {
                            "type": "string",
                            "description": "Sender email (must be verified in Brevo). Default: uses first sender.",
                        },
                        "from_name": {
                            "type": "string",
                            "description": "Sender display name.",
                        },
                    },
                    "required": ["to", "subject", "html_body"],
                },
            ),
            types.Tool(
                name="events",
                description="Get recent email events (sent, delivered, opened, clicked, bounced).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of events to return (default: 20, max: 100).",
                            "default": 20,
                        },
                        "event_type": {
                            "type": "string",
                            "description": "Filter by event type: sent, delivered, opened, clicked, bounced, etc.",
                        },
                    },
                    "required": [],
                },
            ),
        ]

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate API key by fetching account info."""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{BREVO_API_BASE}/account",
                headers={"api-key": creds.get("api_key", ""), "accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return None  # Valid
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "403" in err_str:
                return {"valid": False, "message": "API key is invalid or revoked."}
            # Network errors don't mean invalid creds
            return None
        return None

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        api_key = args.get("api_key", "").strip()
        if not api_key:
            return json.dumps({"error": "api_key is required."})

        if not api_key.startswith("xkeysib-"):
            return json.dumps({"error": "Invalid Brevo API key format. Must start with xkeysib-."})

        # Validate key
        validation = await self._validate_credentials({"api_key": api_key})
        if validation and not validation.get("valid"):
            return json.dumps({"error": validation.get("message", "API key validation failed.")})

        self._save_creds({"api_key": api_key})
        return json.dumps({"status": "connected", "message": "Brevo connected successfully."})

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "send":
            return await self._send_email(args)
        elif name == "events":
            return await self._get_events(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    async def _send_email(self, args: Dict[str, Any]) -> str:
        creds = self._require_creds()
        if not creds:
            return self._no_creds_error()

        import urllib.request
        sender = {"email": args.get("from_email", "")}
        from_name = args.get("from_name")
        if from_name:
            sender["name"] = from_name
        payload = {
            "sender": sender,
            "to": [{"email": args["to"], "name": args.get("to_name", "")}],
            "subject": args["subject"],
            "htmlContent": args.get("html_body") or args.get("content") or args.get("body", "<p></p>"),
        }

        # If no from_email specified, use the account's default sender
        if not payload["sender"]["email"]:
            del payload["sender"]  # Brevo uses default sender if omitted
            # Actually we need at least sender email. Try to get account's senders.
            try:
                senders = self._get_senders(creds["api_key"])
                if senders:
                    payload["sender"] = senders[0]
            except Exception:
                return json.dumps({"error": "No from_email specified and could not fetch default sender."})

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{BREVO_API_BASE}/smtp/email",
            data=data,
            headers={
                "api-key": creds["api_key"],
                "content-type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                return json.dumps({"status": "sent", "message_id": result.get("messageId", "")})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return json.dumps({"error": f"HTTP {e.code}: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to send email: {e}"})

    async def _get_events(self, args: Dict[str, Any]) -> str:
        creds = self._require_creds()
        if not creds:
            return self._no_creds_error()

        import urllib.request
        limit = min(args.get("limit", 20), 100)
        params = f"limit={limit}"
        if args.get("event_type"):
            params += f"&event={args['event_type']}"

        req = urllib.request.Request(
            f"{BREVO_API_BASE}/smtp/statistics/events?{params}",
            headers={"api-key": creds["api_key"], "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                events = json.loads(resp.read())
                return json.dumps({"events": events, "count": len(events)})
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch events: {e}"})

    def _get_senders(self, api_key: str) -> List[Dict]:
        """Fetch account's verified senders."""
        import urllib.request
        req = urllib.request.Request(
            f"{BREVO_API_BASE}/senders",
            headers={"api-key": api_key, "accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return [{"email": s["email"], "name": s.get("name", "")} for s in data.get("senders", []) if s.get("active", True)]


if __name__ == "__main__":
    BrevoMCPServer().start()
