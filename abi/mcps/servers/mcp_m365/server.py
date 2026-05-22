"""Microsoft 365 MCP Server — Graph API accessor.

Tools:
    m365_status    — Check auth status and tenant info
    m365_login     — Initiate device code flow for tenant
    m365_logout    — Remove stored tokens
    m365_read_mail — Read recent emails
    m365_send_mail — Send an email via Exchange
    m365_calendar  — Get upcoming calendar events

Auth: MSAL device code flow. Agent gives owner URL+code, owner authenticates in browser.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from abi.mcps.base import ABIMCPServer
from mcp import types

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Default scopes — used unless overridden at login
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
                    "description": "Azure AD application (client) ID for this tenant.",
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

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate by trying to fetch user profile."""
        token = creds.get("access_token")
        if not token:
            return {"valid": False, "message": "No access token stored."}

        try:
            import urllib.request
            req = urllib.request.Request(
                f"{GRAPH_BASE}/me",
                headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return None  # Valid
        except Exception as e:
            err_str = str(e)
            if "401" in err_str:
                # Try refresh
                refreshed = self._try_refresh(creds)
                if refreshed:
                    return None
                return {"valid": False, "message": "Token expired and refresh failed."}
            return None  # Network errors don't mean invalid

    def _try_refresh(self, creds: Dict[str, Any]) -> bool:
        """Try to refresh the access token using stored refresh token."""
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            return False

        try:
            import urllib.request
            import urllib.error
            import urllib.parse as _up
            data = _up.urlencode({
                "client_id": creds.get("client_id", ""),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(creds.get("scopes", DEFAULT_SCOPES)),
            }).encode()

            req = urllib.request.Request(
                f"https://login.microsoftonline.com/{creds.get('tenant_id', 'common')}/oauth2/v2.0/token",
                data=data,
                headers={"content-type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                creds["access_token"] = result["access_token"]
                if "refresh_token" in result:
                    creds["refresh_token"] = result["refresh_token"]
                creds["expires_at"] = __import__("time").time() + result.get("expires_in", 3600)
                self._save_creds(creds)
                return True
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if "AADSTS70008" in body or "AADSTS700082" in body:
                # Refresh token expired — need full re-auth
                return False
            return False
        except Exception:
            return False

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        client_id = args.get("client_id", "").strip()
        tenant_id = args.get("tenant_id", "").strip()
        if not client_id:
            return json.dumps({"error": "client_id is required. Provide the Azure AD application (client) ID."})
        if not tenant_id:
            return json.dumps({"error": "tenant_id is required."})

        try:
            import urllib.request
            # Initiate device code flow (must be form-encoded, not JSON)
            import urllib.parse
            data = urllib.parse.urlencode({
                "client_id": client_id,
                "scope": " ".join(DEFAULT_SCOPES),
            }).encode()

            req = urllib.request.Request(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode",
                data=data,
                headers={"content-type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                device_code_resp = json.loads(resp.read())

            device_code = device_code_resp["device_code"]
            user_code = device_code_resp["user_code"]
            verification_url = device_code_resp["verification_uri"]

            # Poll for token completion
            import time
            interval = device_code_resp.get("interval", 5)
            expires_in = device_code_resp.get("expires_in", 900)
            start = time.time()

            while time.time() - start < expires_in:
                time.sleep(interval)
                try:
                    import urllib.parse as _up
                    token_data = _up.urlencode({
                        "client_id": client_id,
                        "grant_type": "urn:ietf:params:oauth:grants:device_code",
                        "device_code": device_code,
                    }).encode()

                    token_req = urllib.request.Request(
                        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                        data=token_data,
                        headers={"content-type": "application/x-www-form-urlencoded"},
                        method="POST",
                    )
                    with urllib.request.urlopen(token_req, timeout=15) as token_resp:
                        token_result = json.loads(token_resp.read())

                        # Store tokens + app config for refresh
                        self._save_creds({
                            "access_token": token_result["access_token"],
                            "refresh_token": token_result.get("refresh_token", ""),
                            "client_id": client_id,
                            "tenant_id": tenant_id,
                            "scopes": DEFAULT_SCOPES,
                            "expires_at": time.time() + token_result.get("expires_in", 3600),
                        })
                        return json.dumps({
                            "status": "connected",
                            "message": f"M365 connected for tenant {tenant_id}.",
                        })
                except Exception as poll_err:
                    err_str = str(poll_err)
                    if "authorization_pending" in err_str:
                        continue  # User hasn't completed yet
                    elif "slow_down" in err_str:
                        time.sleep(interval)
                        continue
                    elif "expired_token" in err_str:
                        return json.dumps({"error": "Device code expired. Please try login again."})
                    # Other errors might be the actual success
                    raise

            return json.dumps({"error": "Device code flow timed out. Please try again."})

        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 400 and "AADSTS500011" in body:
                return json.dumps({"error": f"Tenant '{tenant_id}' not found. Use the exact tenant ID (e.g., contoso.onmicrosoft.com) or GUID."})
            if e.code == 400:
                return json.dumps({"error": f"Bad request starting device code flow: {body}"})
            return json.dumps({"error": f"HTTP {e.code} during login: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Login failed: {e}"})

    async def _handle_login_no_wait(self, args: Dict[str, Any]) -> str:
        """Alternative: return device code info for owner to complete, don't wait."""
        client_id = args.get("client_id", "").strip()
        tenant_id = args.get("tenant_id", "").strip()
        if not client_id:
            return json.dumps({"error": "client_id is required."})
        if not tenant_id:
            return json.dumps({"error": "tenant_id is required."})

        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({
            "client_id": client_id,
            "scope": " ".join(DEFAULT_SCOPES),
        }).encode()

        req = urllib.request.Request(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/devicecode",
            data=data,
            headers={"content-type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            device_resp = json.loads(resp.read())

        # Store device code for later polling
        self._save_creds({
            "device_code": device_resp["device_code"],
            "tenant_id": tenant_id,
            "status": "pending_auth",
        })

        return json.dumps({
            "status": "pending_auth",
            "message": f"Go to {device_resp['verification_uri']} and enter code: {device_resp['user_code']}",
            "user_code": device_resp["user_code"],
            "url": device_resp["verification_uri"],
            "expires_in": device_resp.get("expires_in", 900),
        })

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "read_mail":
            return await self._read_mail(args)
        elif name == "send_mail":
            return await self._send_mail(args)
        elif name == "calendar":
            return await self._calendar(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    def _get_token(self) -> Optional[str]:
        """Get a valid access token, refreshing if needed."""
        creds = self._require_creds()
        if not creds:
            return None
        token = creds.get("access_token")
        if not token:
            return None
        # Auto-refresh if expired
        import time
        if creds.get("expires_at", 0) < time.time() + 300:
            self._try_refresh(creds)
            creds = self._require_creds()
            if not creds:
                return None
            token = creds.get("access_token")
        return token

    async def _read_mail(self, args: Dict[str, Any]) -> str:
        token = self._get_token()
        if not token:
            return self._no_creds_error()

        import urllib.request
        import urllib.error
        folder = args.get("folder", "Inbox")
        limit = min(args.get("limit", 10), 50)
        params = f"$top={limit}&$orderby=receivedDateTime desc&$select=subject,from,receivedDateTime,isRead,bodyPreview"
        if args.get("unread_only"):
            params += "&$filter=isRead eq false"

        req = urllib.request.Request(
            f"{GRAPH_BASE}/me/mailFolders/{folder}/messages?{params}",
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

        # Validate required fields
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
        from datetime import datetime, timezone, timedelta

        days = max(1, min(args.get("days", 7), 365))
        now = datetime.now(timezone.utc).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        params = f"$filter=start/dateTime ge '{now}' and end/dateTime le '{end}'&$orderby=start/dateTime&$select=subject,start,end,organizer,location"
        req = urllib.request.Request(
            f"{GRAPH_BASE}/me/calendarView?{params}",
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
