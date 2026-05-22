"""Microsoft 365 MCP Server — Graph API accessor.

Tools:
    m365_status    — Check auth status and tenant info
    m365_login     — Initiate device code flow for tenant
    m365_logout    — Remove stored tokens
    m365_read_mail — Read recent emails
    m365_send_mail — Send an email via Exchange
    m365_calendar  — Get upcoming calendar events

Auth: MSAL device code flow. Agent gives owner URL+code, owner authenticates in browser.
Uses the official MSAL library for all OAuth operations.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from abi.mcps.base import ABIMCPServer
from mcp import types

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

DEFAULT_SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Calendars.Read",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Chat.Read",
    "https://graph.microsoft.com/Files.Read.All",
]


class M365MCPServer(ABIMCPServer):
    SERVICE_NAME = "m365"

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "description": "Azure AD application (client) ID.",
                },
                "tenant_id": {
                    "type": "string",
                    "description": "M365 tenant ID or domain (e.g., contoso.onmicrosoft.com).",
                },
            },
            "required": ["client_id", "tenant_id"],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            types.Tool(
                name="read_mail",
                description="Read recent emails from the connected mailbox.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "Mail folder name (default: Inbox).",
                            "default": "Inbox",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of emails to return (default: 10, max: 50).",
                            "default": 10,
                        },
                        "unread_only": {
                            "type": "boolean",
                            "description": "Only return unread emails (default: false).",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="send_mail",
                description="Send an email via Exchange Online.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address.",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Email body (HTML).",
                        },
                        "cc": {
                            "type": "string",
                            "description": "CC recipients (comma-separated emails).",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            types.Tool(
                name="calendar",
                description="Get upcoming calendar events.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Number of days to look ahead (default: 7).",
                            "default": 7,
                        },
                    },
                    "required": [],
                },
            ),
        ]

    def _get_msal_app(self, client_id: str, tenant_id: str):
        """Create or get cached MSAL PublicClientApplication."""
        import msal
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        return msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=self._get_msal_cache(client_id, tenant_id),
        )

    def _get_msal_cache(self, client_id: str, tenant_id: str):
        """Build MSAL cache from stored credentials."""
        import msal
        creds = self._require_creds()
        cache = msal.SerializableTokenCache()
        if creds and "msal_cache" in creds:
            cache.deserialize(creds["msal_cache"])
        return cache

    def _save_msal_cache(self, app, client_id: str, tenant_id: str):
        """Save MSAL cache back to credential store."""
        if app.token_cache.has_state_changed:
            creds = self._require_creds() or {}
            creds["msal_cache"] = app.token_cache.serialize()
            creds["client_id"] = client_id
            creds["tenant_id"] = tenant_id
            self._save_creds(creds)

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate by trying to acquire a token silently, or complete pending device code flow."""
        client_id = creds.get("client_id", "")
        tenant_id = creds.get("tenant_id", "")
        if not client_id or not tenant_id:
            return {"valid": False, "message": "No client_id or tenant_id stored."}

        try:
            import msal
            import asyncio
            loop = asyncio.get_event_loop()
            app = self._get_msal_app(client_id, tenant_id)

            # First: try to complete a pending device code flow
            flow = creds.get("pending_flow")
            if flow:
                result = await loop.run_in_executor(
                    None, lambda: app.acquire_token_by_device_flow(flow)
                )
                if result and "access_token" in result:
                    # Device code completed — store the token
                    creds.pop("pending_flow", None)
                    creds["msal_cache"] = app.token_cache.serialize()
                    self._save_creds(creds)
                    return None  # Valid!
                error = result.get("error", "")
                desc = result.get("error_description", "")
                if "authorization_pending" in error or "authorization_pending" in desc:
                    return {"valid": False, "message": "Authentication still pending. Complete the device code in your browser, then check status again."}
                if "expired_token" in error:
                    creds.pop("pending_flow", None)
                    self._save_creds(creds)
                    return {"valid": False, "message": "Device code expired. Call m365_login to start a new flow."}
                # Other error
                return {"valid": False, "message": f"Device code flow error: {desc}"}

            # No pending flow — try silent token acquisition
            accounts = app.get_accounts()
            if not accounts:
                return {"valid": False, "message": "Not authenticated. Call m365_login to start device code flow."}

            result = app.acquire_token_silent(scopes=DEFAULT_SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._save_msal_cache(app, client_id, tenant_id)
                return None
            return {"valid": False, "message": "Token expired and refresh failed. Call m365_login."}
        except Exception as e:
            return {"valid": False, "message": f"Validation error: {e}"}

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        client_id = args.get("client_id", "").strip()
        tenant_id = args.get("tenant_id", "").strip()
        if not client_id:
            return json.dumps({"error": "client_id is required. Provide the Azure AD application (client) ID."})
        if not tenant_id:
            return json.dumps({"error": "tenant_id is required."})

        try:
            import msal
            import asyncio

            app = self._get_msal_app(client_id, tenant_id)

            # Run MSAL in thread executor to avoid blocking the async event loop
            loop = asyncio.get_event_loop()
            flow = await loop.run_in_executor(None, lambda: app.initiate_device_flow(scopes=DEFAULT_SCOPES))

            if "error" in flow:
                desc = flow.get("error_description", flow.get("error", "Unknown error"))
                return json.dumps({"error": f"Device code flow failed: {desc}"})

            # Store the flow + app config so we can poll later
            self._save_creds({
                "client_id": client_id,
                "tenant_id": tenant_id,
                "msal_cache": app.token_cache.serialize(),
                "pending_flow": flow,
            })

            # Return instructions for the human (non-blocking)
            return json.dumps({
                "status": "pending_auth",
                "message": f"Go to {flow['verification_uri']} and enter code: {flow['user_code']}",
                "user_code": flow["user_code"],
                "url": flow["verification_uri"],
                "expires_in": flow.get("expires_in", 900),
                "hint": "After authenticating in the browser, call m365_status to verify connection.",
            })
        except Exception as e:
            return json.dumps({"error": f"Login failed: {e}"})

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "read_mail":
            return await self._read_mail(args)
        elif name == "send_mail":
            return await self._send_mail(args)
        elif name == "calendar":
            return await self._calendar(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    def _get_token(self) -> Optional[str]:
        """Get a valid access token using MSAL (auto-refresh). Called from async handlers."""
        creds = self._require_creds()
        if not creds:
            return None

        client_id = creds.get("client_id", "")
        tenant_id = creds.get("tenant_id", "")
        if not client_id or not tenant_id:
            return None

        import msal
        app = self._get_msal_app(client_id, tenant_id)
        accounts = app.get_accounts()
        if not accounts:
            # Try to complete pending device code flow
            flow = creds.get("pending_flow")
            if flow:
                result = app.acquire_token_by_device_flow(flow)
                if result and "access_token" in result:
                    creds.pop("pending_flow", None)
                    creds["msal_cache"] = app.token_cache.serialize()
                    self._save_creds(creds)
                    return result["access_token"]
                return None
            return None

        result = app.acquire_token_silent(scopes=DEFAULT_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            self._save_msal_cache(app, client_id, tenant_id)
            return result["access_token"]
        return None

    async def _read_mail(self, args: Dict[str, Any]) -> str:
        token = self._get_token()
        if not token:
            return self._no_creds_error()

        import urllib.request
        import urllib.error
        import urllib.parse
        folder = args.get("folder", "Inbox")
        limit = min(args.get("limit", 10), 50)
        qparams = urllib.parse.urlencode({
            "$top": str(limit),
            "$orderby": "receivedDateTime desc",
            "$select": "subject,from,receivedDateTime,isRead,bodyPreview",
            **({"$filter": "isRead eq false"} if args.get("unread_only") else {}),
        })
        url = f"{GRAPH_BASE}/me/mailFolders/{urllib.parse.quote(folder, safe='')}/messages?{qparams}"
        req = urllib.request.Request(url,
            headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                emails = []
                for msg in data.get("value", []):
                    emails.append({
                        "subject": msg.get("subject", ""),
                        "from": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                        "received": msg.get("receivedDateTime", ""),
                        "is_read": msg.get("isRead", True),
                        "preview": msg.get("bodyPreview", "")[:200],
                    })
                return json.dumps({"emails": emails, "count": len(emails)})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 401:
                return json.dumps({"error": "Token expired. Call m365_login to re-authenticate."})
            if e.code == 404:
                return json.dumps({"error": f"Mail folder '{folder}' not found. Use folder name like Inbox, SentItems, Drafts, DeletedItems, or JunkEmail."})
            return json.dumps({"error": f"HTTP {e.code} reading mail: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to read mail: {e}"})

    async def _send_mail(self, args: Dict[str, Any]) -> str:
        token = self._get_token()
        if not token:
            return self._no_creds_error()

        missing = [f for f in ["to", "subject", "body"] if not args.get(f)]
        if missing:
            return json.dumps({"error": f"Missing required fields: {', '.join(missing)}. Provide to (email address), subject, and body (HTML content)."})

        import urllib.request
        import urllib.error
        to_addr = args["to"].strip()
        if "@" not in to_addr:
            return json.dumps({"error": f"Invalid recipient email: '{to_addr}'. Must be a valid email address."})

        payload = {
            "message": {
                "subject": args["subject"],
                "body": {"contentType": "HTML", "content": args["body"]},
                "toRecipients": [{"emailAddress": {"address": to_addr}}],
            },
        }
        if args.get("cc"):
            cc_list = [{"emailAddress": {"address": e.strip()}} for e in args["cc"].split(",")]
            payload["message"]["ccRecipients"] = cc_list

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{GRAPH_BASE}/me/sendMail",
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.dumps({"status": "sent"})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 401:
                return json.dumps({"error": "Token expired. Call m365_login to re-authenticate."})
            if e.code == 403:
                return json.dumps({"error": f"Permission denied. The app needs Mail.Send permission. Details: {body}"})
            return json.dumps({"error": f"HTTP {e.code} sending mail: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to send mail: {e}"})

    async def _calendar(self, args: Dict[str, Any]) -> str:
        token = self._get_token()
        if not token:
            return self._no_creds_error()

        import urllib.request
        import urllib.error
        import urllib.parse
        from datetime import datetime, timezone, timedelta

        days = max(1, min(args.get("days", 7), 365))
        now = datetime.now(timezone.utc).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        qparams = urllib.parse.urlencode({
            "$filter": f"start/dateTime ge '{now}' and end/dateTime le '{end}'",
            "$orderby": "start/dateTime",
            "$select": "subject,start,end,organizer,location",
        })
        req = urllib.request.Request(
            f"{GRAPH_BASE}/me/calendarView?{qparams}",
            headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                events = []
                for evt in data.get("value", []):
                    events.append({
                        "subject": evt.get("subject", ""),
                        "start": evt.get("start", {}).get("dateTime", ""),
                        "end": evt.get("end", {}).get("dateTime", ""),
                        "organizer": evt.get("organizer", {}).get("emailAddress", {}).get("address", ""),
                        "location": evt.get("location", {}).get("displayName", ""),
                    })
                return json.dumps({"events": events, "count": len(events)})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 401:
                return json.dumps({"error": "Token expired. Call m365_login to re-authenticate."})
            if e.code == 403:
                return json.dumps({"error": f"Permission denied. The app needs Calendars.Read permission. Details: {body}"})
            return json.dumps({"error": f"HTTP {e.code} fetching calendar: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch calendar: {e}"})


if __name__ == "__main__":
    M365MCPServer().start()
