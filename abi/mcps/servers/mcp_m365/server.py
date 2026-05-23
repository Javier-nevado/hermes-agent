"""Microsoft 365 MCP Server — Graph API accessor.

Tools:
    m365_status    — Check auth status and tenant info
    m365_login     — Initiate device code flow for tenant
    m365_logout    — Remove stored tokens
    m365_read_mail — Read recent emails
    m365_send_mail — Send an email via Exchange
    m365_calendar  — Get upcoming calendar events
    m365_tasks     — List, create, complete To Do tasks
    m365_teams     — List joined Teams and channels
    m365_teams_messages — List/send channel messages
    m365_chats     — List 1:1 and group chats, send messages
    m365_drive     — List, upload, download OneDrive files
    m365_contacts  — List and search contacts

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
    "https://graph.microsoft.com/Chat.ReadWrite",
    "https://graph.microsoft.com/Files.Read.All",
    "https://graph.microsoft.com/Files.ReadWrite.All",
    "https://graph.microsoft.com/Tasks.Read",
    "https://graph.microsoft.com/Tasks.ReadWrite",
    "https://graph.microsoft.com/Contacts.Read",
    "https://graph.microsoft.com/ChannelMessage.Send",
    "https://graph.microsoft.com/Team.ReadBasic.All",
    "https://graph.microsoft.com/Channel.ReadBasic.All",
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
            # --- Mail ---
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
            # --- Calendar ---
            types.Tool(
                name="calendar",
                description="Get calendar events for a date range.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY-MM-DD format (default: today).",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in YYYY-MM-DD format (default: start_date + 7 days).",
                        },
                        "days": {
                            "type": "integer",
                            "description": "Number of days to look ahead from start_date (default: 7). Ignored if end_date is set.",
                            "default": 7,
                        },
                    },
                    "required": [],
                },
            ),
            # --- Tasks / To Do ---
            types.Tool(
                name="list_task_lists",
                description="List all Microsoft To Do task lists.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="list_tasks",
                description="List tasks from a To Do list. Defaults to first list if no list_id provided.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "list_id": {
                            "type": "string",
                            "description": "To Do list ID (optional, defaults to first list).",
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by status: notStarted, inProgress, completed.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max tasks to return (default: 50).",
                            "default": 50,
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="create_task",
                description="Create a new task in a To Do list.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Task title.",
                        },
                        "list_id": {
                            "type": "string",
                            "description": "To Do list ID (optional, defaults to first list).",
                        },
                        "body": {
                            "type": "string",
                            "description": "Task description (optional).",
                        },
                        "due_date": {
                            "type": "string",
                            "description": "Due date in YYYY-MM-DD format (optional).",
                        },
                        "importance": {
                            "type": "string",
                            "description": "Importance: low, normal, high (default: normal).",
                            "default": "normal",
                        },
                    },
                    "required": ["title"],
                },
            ),
            types.Tool(
                name="complete_task",
                description="Mark a task as completed.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID to complete.",
                        },
                        "list_id": {
                            "type": "string",
                            "description": "To Do list ID (optional, defaults to first list).",
                        },
                    },
                    "required": ["task_id"],
                },
            ),
            # --- Teams (channels) ---
            types.Tool(
                name="list_teams",
                description="List all Microsoft Teams the user has joined.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="list_channels",
                description="List channels in a Microsoft Team.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "team_id": {
                            "type": "string",
                            "description": "Team ID.",
                        },
                    },
                    "required": ["team_id"],
                },
            ),
            types.Tool(
                name="list_channel_messages",
                description="List recent messages in a Teams channel.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "team_id": {"type": "string", "description": "Team ID."},
                        "channel_id": {"type": "string", "description": "Channel ID."},
                        "limit": {
                            "type": "integer",
                            "description": "Max messages (default: 25).",
                            "default": 25,
                        },
                    },
                    "required": ["team_id", "channel_id"],
                },
            ),
            types.Tool(
                name="send_channel_message",
                description="Send a message to a Teams channel.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "team_id": {"type": "string", "description": "Team ID."},
                        "channel_id": {"type": "string", "description": "Channel ID."},
                        "content": {"type": "string", "description": "Message content (text or HTML)."},
                    },
                    "required": ["team_id", "channel_id", "content"],
                },
            ),
            # --- Teams chats (1:1 and group) ---
            types.Tool(
                name="list_chats",
                description="List 1:1 and group chats in Microsoft Teams.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max chats to return (default: 25).",
                            "default": 25,
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="list_chat_messages",
                description="List messages in a 1:1 or group chat.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chat_id": {"type": "string", "description": "Chat ID."},
                        "limit": {
                            "type": "integer",
                            "description": "Max messages (default: 25).",
                            "default": 25,
                        },
                    },
                    "required": ["chat_id"],
                },
            ),
            types.Tool(
                name="send_chat_message",
                description="Send a message in a 1:1 or group Teams chat.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chat_id": {"type": "string", "description": "Chat ID."},
                        "content": {"type": "string", "description": "Message content."},
                    },
                    "required": ["chat_id", "content"],
                },
            ),
            # --- Drive / OneDrive ---
            types.Tool(
                name="list_drive",
                description="List files and folders in OneDrive root or a specific folder.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Folder path relative to root (optional, defaults to root).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max items (default: 50).",
                            "default": 50,
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="download_file",
                description="Download a file from OneDrive by its path or item ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to OneDrive root.",
                        },
                        "item_id": {
                            "type": "string",
                            "description": "Drive item ID (alternative to path).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="upload_file",
                description="Upload a file to OneDrive. Content is base64-encoded.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Destination path in OneDrive (e.g., Documents/report.pdf).",
                        },
                        "content_b64": {
                            "type": "string",
                            "description": "Base64-encoded file content.",
                        },
                    },
                    "required": ["path", "content_b64"],
                },
            ),
            types.Tool(
                name="create_folder",
                description="Create a folder in OneDrive.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Folder path (e.g., Projects/NewProject).",
                        },
                        "name": {
                            "type": "string",
                            "description": "Folder name (alternative to path, creates in root).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="search_drive",
                description="Search for files in OneDrive by name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (file name or text content).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default: 25).",
                            "default": 25,
                        },
                    },
                    "required": ["query"],
                },
            ),
            # --- Contacts ---
            types.Tool(
                name="list_contacts",
                description="List contacts from the user's address book.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max contacts (default: 50).",
                            "default": 50,
                        },
                        "search": {
                            "type": "string",
                            "description": "Search by display name or email (optional).",
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

            loop = asyncio.get_event_loop()
            flow = await loop.run_in_executor(None, lambda: app.initiate_device_flow(scopes=DEFAULT_SCOPES))

            if "error" in flow:
                desc = flow.get("error_description", flow.get("error", "Unknown error"))
                return json.dumps({"error": f"Device code flow failed: {desc}"})

            self._save_creds({
                "client_id": client_id,
                "tenant_id": tenant_id,
                "msal_cache": app.token_cache.serialize(),
                "pending_flow": flow,
            })

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
        dispatch = {
            "read_mail": self._read_mail,
            "send_mail": self._send_mail,
            "calendar": self._calendar,
            # Tasks
            "list_task_lists": self._list_task_lists,
            "list_tasks": self._list_tasks,
            "create_task": self._create_task,
            "complete_task": self._complete_task,
            # Teams (channels)
            "list_teams": self._list_teams,
            "list_channels": self._list_channels,
            "list_channel_messages": self._list_channel_messages,
            "send_channel_message": self._send_channel_message,
            # Teams chats (1:1/group)
            "list_chats": self._list_chats,
            "list_chat_messages": self._list_chat_messages,
            "send_chat_message": self._send_chat_message,
            # Drive / OneDrive
            "list_drive": self._list_drive,
            "download_file": self._download_file,
            "upload_file": self._upload_file,
            "create_folder": self._create_folder,
            "search_drive": self._search_drive,
            # Contacts
            "list_contacts": self._list_contacts,
        }
        handler = dispatch.get(name)
        if handler:
            return await handler(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    def _get_token(self) -> Optional[str]:
        """Get a valid access token using MSAL (auto-refresh)."""
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
            flow = creds.get("pending_flow")
            if flow:
                result = app.acquire_token_by_device_flow(flow)
                if result and "access_token" in result:
                    creds.pop("pending_flow", None)
                    creds["msal_cache"] = app.token_cache.serialize()
                    self._save_creds(creds)
                    return result["access_token"]
            return None

        result = app.acquire_token_silent(scopes=DEFAULT_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            self._save_msal_cache(app, client_id, tenant_id)
            return result["access_token"]
        return None

    def _graph_get(self, endpoint: str, params: dict = None, headers: dict = None, token: str = None) -> dict:
        """Make a GET request to Graph API. Returns parsed JSON or raises."""
        import urllib.request, urllib.error, urllib.parse
        if not token:
            token = self._get_token()
        if not token:
            return {"error": self._no_creds_error()}
        qstr = urllib.parse.urlencode(params) if params else ""
        url = f"{GRAPH_BASE}{endpoint}{'?' + qstr if qstr else ''}"
        hdrs = {"Authorization": f"Bearer {token}", "accept": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

    def _graph_post(self, endpoint: str, payload: dict, token: str = None) -> dict:
        """Make a POST request to Graph API. Returns parsed JSON or raises."""
        import urllib.request, urllib.error
        if not token:
            token = self._get_token()
        if not token:
            return {"error": self._no_creds_error()}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{GRAPH_BASE}{endpoint}", data=data,
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

    def _graph_patch(self, endpoint: str, payload: dict, token: str = None) -> dict:
        """Make a PATCH request to Graph API. Returns parsed JSON or raises."""
        import urllib.request, urllib.error
        if not token:
            token = self._get_token()
        if not token:
            return {"error": self._no_creds_error()}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{GRAPH_BASE}{endpoint}", data=data,
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

    # =========================================================================
    # Mail
    # =========================================================================

    async def _read_mail(self, args: Dict[str, Any]) -> str:
        token = self._get_token()
        if not token:
            return self._no_creds_error()

        import urllib.request, urllib.error, urllib.parse
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

        import urllib.request, urllib.error
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
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
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

    # =========================================================================
    # Calendar
    # =========================================================================

    async def _calendar(self, args: Dict[str, Any]) -> str:
        token = self._get_token()
        if not token:
            return self._no_creds_error()

        import urllib.request, urllib.error, urllib.parse
        from datetime import datetime, timezone, timedelta

        start_date_str = args.get("start_date", "").strip()
        end_date_str = args.get("end_date", "").strip()
        days = max(1, min(args.get("days", 7), 365))

        if start_date_str:
            try:
                base = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return json.dumps({"error": f"Invalid start_date '{start_date_str}'. Use YYYY-MM-DD format."})
        else:
            base = datetime.now(timezone.utc)

        if end_date_str:
            try:
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc, hour=23, minute=59, second=59
                )
            except ValueError:
                return json.dumps({"error": f"Invalid end_date '{end_date_str}'. Use YYYY-MM-DD format."})
        else:
            end_dt = base + timedelta(days=days)

        now = base.isoformat()
        end = end_dt.isoformat()

        qparams = urllib.parse.urlencode({
            "startDateTime": now,
            "endDateTime": end,
            "$select": "subject,start,end,organizer,location,responseStatus,isAllDay,recurrence",
        })
        req = urllib.request.Request(
            f"{GRAPH_BASE}/me/calendarView?{qparams}",
            headers={
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
                "Prefer": 'outlook.timezone="Europe/Malta"',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                events = []
                for evt in data.get("value", []):
                    start_obj = evt.get("start", {})
                    end_obj = evt.get("end", {})
                    events.append({
                        "subject": evt.get("subject", ""),
                        "start": start_obj.get("dateTime", ""),
                        "start_timezone": start_obj.get("timeZone", ""),
                        "end": end_obj.get("dateTime", ""),
                        "end_timezone": end_obj.get("timeZone", ""),
                        "organizer": evt.get("organizer", {}).get("emailAddress", {}).get("address", ""),
                        "location": evt.get("location", {}).get("displayName", ""),
                        "response": evt.get("responseStatus", {}).get("response", ""),
                    })
                return json.dumps({"events": events, "count": len(events)})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 401:
                return json.dumps({"error": "Token expired. Call m365_login to re-authenticate."})
            return json.dumps({"error": f"HTTP {e.code} fetching calendar: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch calendar: {e}"})

    # =========================================================================
    # Tasks / To Do
    # =========================================================================

    async def _list_task_lists(self, args: Dict[str, Any]) -> str:
        try:
            data = self._graph_get("/me/todo/lists")
            lists = [{"id": l.get("id", ""), "name": l.get("displayName", ""), "isOwner": l.get("isOwner", True), "isShared": l.get("isShared", False)} for l in data.get("value", [])]
            return json.dumps({"lists": lists, "count": len(lists)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list task lists: {e}"})

    async def _list_tasks(self, args: Dict[str, Any]) -> str:
        list_id = args.get("list_id", "")
        status = args.get("status", "")
        limit = min(args.get("limit", 50), 200)

        try:
            if not list_id:
                # Default to first list
                data = self._graph_get("/me/todo/lists")
                lists = data.get("value", [])
                if not lists:
                    return json.dumps({"tasks": [], "count": 0, "hint": "No To Do lists found. Create one first."})
                list_id = lists[0]["id"]

            params = {"$top": str(limit)}
            if status:
                params["$filter"] = f"status eq '{status}'"

            data = self._graph_get(f"/me/todo/lists/{list_id}/tasks", params)
            tasks = []
            for t in data.get("value", []):
                tasks.append({
                    "id": t.get("id", ""),
                    "title": t.get("title", ""),
                    "status": t.get("status", ""),
                    "importance": t.get("importance", ""),
                    "due": t.get("dueDateTime", {}).get("dateTime", "") if t.get("dueDateTime") else "",
                    "body": t.get("body", {}).get("content", "")[:200] if t.get("body") else "",
                    "created": t.get("createdDateTime", ""),
                })
            return json.dumps({"tasks": tasks, "count": len(tasks), "list_id": list_id})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list tasks: {e}"})

    async def _create_task(self, args: Dict[str, Any]) -> str:
        title = args.get("title", "").strip()
        if not title:
            return json.dumps({"error": "title is required."})

        list_id = args.get("list_id", "")
        try:
            if not list_id:
                data = self._graph_get("/me/todo/lists")
                lists = data.get("value", [])
                if not lists:
                    return json.dumps({"error": "No To Do lists found. Create one via the M365 To Do app first."})
                list_id = lists[0]["id"]

            payload = {"title": title, "importance": args.get("importance", "normal")}
            if args.get("body"):
                payload["body"] = {"content": args["body"], "contentType": "text"}
            if args.get("due_date"):
                from datetime import datetime, timezone
                try:
                    dt = datetime.strptime(args["due_date"], "%Y-%m-%d")
                    payload["dueDateTime"] = {"dateTime": dt.isoformat(), "timeZone": "UTC"}
                except ValueError:
                    return json.dumps({"error": f"Invalid due_date '{args['due_date']}'. Use YYYY-MM-DD format."})

            result = self._graph_post(f"/me/todo/lists/{list_id}/tasks", payload)
            return json.dumps({
                "status": "created",
                "task_id": result.get("id", ""),
                "title": result.get("title", title),
            })
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to create task: {e}"})

    async def _complete_task(self, args: Dict[str, Any]) -> str:
        task_id = args.get("task_id", "").strip()
        if not task_id:
            return json.dumps({"error": "task_id is required."})
        list_id = args.get("list_id", "")
        try:
            if not list_id:
                data = self._graph_get("/me/todo/lists")
                lists = data.get("value", [])
                if not lists:
                    return json.dumps({"error": "No To Do lists found."})
                list_id = lists[0]["id"]
            self._graph_patch(f"/me/todo/lists/{list_id}/tasks/{task_id}", {"status": "completed"})
            return json.dumps({"status": "completed", "task_id": task_id})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to complete task: {e}"})

    # =========================================================================
    # Teams (channels)
    # =========================================================================

    async def _list_teams(self, args: Dict[str, Any]) -> str:
        try:
            data = self._graph_get("/me/joinedTeams")
            teams = [{"id": t.get("id", ""), "name": t.get("displayName", ""), "description": t.get("description", "")[:200]} for t in data.get("value", [])]
            return json.dumps({"teams": teams, "count": len(teams)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list teams: {e}"})

    async def _list_channels(self, args: Dict[str, Any]) -> str:
        team_id = args.get("team_id", "").strip()
        if not team_id:
            return json.dumps({"error": "team_id is required. Use list_teams to find team IDs."})
        try:
            data = self._graph_get(f"/teams/{team_id}/channels")
            channels = [{"id": c.get("id", ""), "name": c.get("displayName", ""), "description": c.get("description", "")[:200]} for c in data.get("value", [])]
            return json.dumps({"channels": channels, "count": len(channels)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list channels: {e}"})

    async def _list_channel_messages(self, args: Dict[str, Any]) -> str:
        team_id = args.get("team_id", "").strip()
        channel_id = args.get("channel_id", "").strip()
        if not team_id or not channel_id:
            return json.dumps({"error": "team_id and channel_id are required."})
        limit = min(args.get("limit", 25), 50)
        try:
            import urllib.parse
            params = urllib.parse.urlencode({"$top": str(limit)})
            data = self._graph_get(f"/teams/{team_id}/channels/{channel_id}/messages", {"$top": str(limit)})
            messages = []
            for m in data.get("value", []):
                sender = (m.get("from") or {}).get("user", {})
                body = m.get("body", {}).get("content", "")
                # Strip HTML for preview
                import re
                text = re.sub(r"<[^>]+>", "", body).strip()
                messages.append({
                    "id": m.get("id", ""),
                    "sender": sender.get("displayName", "Unknown"),
                    "text": text[:500],
                    "time": m.get("createdDateTime", ""),
                })
            return json.dumps({"messages": messages, "count": len(messages)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list channel messages: {e}"})

    async def _send_channel_message(self, args: Dict[str, Any]) -> str:
        team_id = args.get("team_id", "").strip()
        channel_id = args.get("channel_id", "").strip()
        content = args.get("content", "").strip()
        if not team_id or not channel_id or not content:
            return json.dumps({"error": "team_id, channel_id, and content are required."})
        try:
            result = self._graph_post(
                f"/teams/{team_id}/channels/{channel_id}/messages",
                {"body": {"contentType": "text", "content": content}},
            )
            return json.dumps({"status": "sent", "message_id": result.get("id", "")})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to send channel message: {e}"})

    # =========================================================================
    # Teams chats (1:1 and group)
    # =========================================================================

    async def _list_chats(self, args: Dict[str, Any]) -> str:
        limit = min(args.get("limit", 25), 50)
        try:
            import urllib.parse
            params = urllib.parse.urlencode({"$top": str(limit), "$orderby": "lastMessagePreview/createdDateTime desc"})
            data = self._graph_get("/me/chats", {"$top": str(limit), "$orderby": "lastMessagePreview/createdDateTime desc"})
            chats = []
            for c in data.get("value", []):
                preview = c.get("lastMessagePreview", {})
                chats.append({
                    "id": c.get("id", ""),
                    "topic": c.get("topic", "Direct message"),
                    "type": c.get("chatType", ""),
                    "last_message": (preview.get("body", {}).get("content", "")[:100] if preview else ""),
                    "last_message_time": (preview.get("createdDateTime", "") if preview else ""),
                })
            return json.dumps({"chats": chats, "count": len(chats)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list chats: {e}"})

    async def _list_chat_messages(self, args: Dict[str, Any]) -> str:
        chat_id = args.get("chat_id", "").strip()
        if not chat_id:
            return json.dumps({"error": "chat_id is required. Use list_chats to find chat IDs."})
        limit = min(args.get("limit", 25), 50)
        try:
            data = self._graph_get(f"/chats/{chat_id}/messages", {"$top": str(limit)})
            messages = []
            import re
            for m in data.get("value", []):
                sender = (m.get("from") or {}).get("user", {})
                body = m.get("body", {}).get("content", "")
                text = re.sub(r"<[^>]+>", "", body).strip()
                if text:
                    messages.append({
                        "id": m.get("id", ""),
                        "sender": sender.get("displayName", "Unknown"),
                        "text": text[:500],
                        "time": m.get("createdDateTime", ""),
                    })
            return json.dumps({"messages": messages, "count": len(messages)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list chat messages: {e}"})

    async def _send_chat_message(self, args: Dict[str, Any]) -> str:
        chat_id = args.get("chat_id", "").strip()
        content = args.get("content", "").strip()
        if not chat_id or not content:
            return json.dumps({"error": "chat_id and content are required."})
        try:
            result = self._graph_post(
                f"/chats/{chat_id}/messages",
                {"body": {"contentType": "text", "content": content}},
            )
            return json.dumps({"status": "sent", "message_id": result.get("id", "")})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to send chat message: {e}"})

    # =========================================================================
    # Drive / OneDrive
    # =========================================================================

    async def _list_drive(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "").strip()
        limit = min(args.get("limit", 50), 200)
        try:
            import urllib.parse
            params = urllib.parse.urlencode({"$top": str(limit), "$select": "name,size,lastModifiedDateTime,folder,file"})
            if path:
                endpoint = f"/me/drive/root:/{urllib.parse.quote(path, safe='')}:/children"
            else:
                endpoint = "/me/drive/root/children"
            data = self._graph_get(endpoint, {"$top": str(limit), "$select": "name,size,lastModifiedDateTime,folder,file"})
            items = []
            for item in data.get("value", []):
                entry = {
                    "name": item.get("name", ""),
                    "size": item.get("size", 0),
                    "last_modified": item.get("lastModifiedDateTime", ""),
                }
                if item.get("folder"):
                    entry["type"] = "folder"
                    entry["child_count"] = item["folder"].get("childCount", 0)
                elif item.get("file"):
                    entry["type"] = "file"
                    entry["mime_type"] = item["file"].get("mimeType", "")
                items.append(entry)
            return json.dumps({"items": items, "count": len(items)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list drive: {e}"})

    async def _download_file(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "").strip()
        item_id = args.get("item_id", "").strip()
        if not path and not item_id:
            return json.dumps({"error": "Provide either path or item_id."})
        try:
            import urllib.request, urllib.parse, base64
            token = self._get_token()
            if not token:
                return self._no_creds_error()
            if item_id:
                url = f"{GRAPH_BASE}/me/drive/items/{item_id}/content"
            else:
                url = f"{GRAPH_BASE}/me/drive/root:/{urllib.parse.quote(path, safe='')}:/content"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = resp.read()
                mime = resp.headers.get("Content-Type", "application/octet-stream")
            # Return metadata + base64 content
            return json.dumps({
                "name": path.split("/")[-1] if path else item_id,
                "size": len(content),
                "mime_type": mime,
                "content_b64": base64.b64encode(content).decode(),
            })
        except Exception as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": f"File not found: {path or item_id}"})
            return json.dumps({"error": f"Failed to download file: {e}"})

    async def _upload_file(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "").strip()
        content_b64 = args.get("content_b64", "").strip()
        if not path or not content_b64:
            return json.dumps({"error": "path and content_b64 are required."})
        try:
            import urllib.request, urllib.parse, base64
            token = self._get_token()
            if not token:
                return self._no_creds_error()
            content = base64.b64decode(content_b64)
            # For files < 4MB, use simple upload
            if len(content) > 4 * 1024 * 1024:
                return json.dumps({"error": f"File too large ({len(content)} bytes). Max 4MB for simple upload. Use resumable upload for larger files."})
            url = f"{GRAPH_BASE}/me/drive/root:/{urllib.parse.quote(path, safe='')}:/content"
            req = urllib.request.Request(
                url, data=content,
                headers={"Authorization": f"Bearer {token}", "content-type": "application/octet-stream"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            return json.dumps({
                "status": "uploaded",
                "name": result.get("name", path.split("/")[-1]),
                "size": result.get("size", len(content)),
                "id": result.get("id", ""),
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to upload file: {e}"})

    async def _create_folder(self, args: Dict[str, Any]) -> str:
        path = args.get("path", "").strip()
        name = args.get("name", "").strip()
        if not path and not name:
            return json.dumps({"error": "Provide either path (e.g., Projects/NewFolder) or name to create in root."})
        try:
            import urllib.parse
            if path:
                # Create nested path by creating the final folder under its parent
                parts = path.rsplit("/", 1)
                if len(parts) == 2:
                    parent_path, folder_name = parts
                    endpoint = f"/me/drive/root:/{urllib.parse.quote(parent_path, safe='')}:/children"
                else:
                    folder_name = parts[0]
                    endpoint = "/me/drive/root/children"
            else:
                folder_name = name
                endpoint = "/me/drive/root/children"

            result = self._graph_post(endpoint, {
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            })
            return json.dumps({
                "status": "created",
                "name": result.get("name", folder_name),
                "id": result.get("id", ""),
            })
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to create folder: {e}"})

    async def _search_drive(self, args: Dict[str, Any]) -> str:
        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "query is required."})
        limit = min(args.get("limit", 25), 50)
        try:
            import urllib.parse
            data = self._graph_get("/me/drive/root/search(q='" + urllib.parse.quote(query, safe='') + "')", {"$top": str(limit)})
            items = []
            for item in data.get("value", []):
                entry = {
                    "name": item.get("name", ""),
                    "size": item.get("size", 0),
                    "last_modified": item.get("lastModifiedDateTime", ""),
                }
                if item.get("folder"):
                    entry["type"] = "folder"
                elif item.get("file"):
                    entry["type"] = "file"
                    entry["mime_type"] = item["file"].get("mimeType", "")
                items.append(entry)
            return json.dumps({"items": items, "count": len(items)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to search drive: {e}"})

    # =========================================================================
    # Contacts
    # =========================================================================

    async def _list_contacts(self, args: Dict[str, Any]) -> str:
        limit = min(args.get("limit", 50), 100)
        search = args.get("search", "").strip()
        try:
            import urllib.parse
            params = {
                "$top": str(limit),
                "$select": "displayName,emailAddresses,companyName,jobTitle,phones",
                "$orderby": "displayName",
            }
            if search:
                params["$search"] = f'"{search}"'
                # $search requires ConsistencyLevel header
                data = self._graph_get("/me/contacts", params, headers={"ConsistencyLevel": "eventual"})
            else:
                data = self._graph_get("/me/contacts", params)
            contacts = []
            for c in data.get("value", []):
                emails = [e.get("address", "") for e in c.get("emailAddresses", []) if e.get("address")]
                phones = [p.get("number", "") for p in c.get("phones", []) if p.get("number")]
                contacts.append({
                    "name": c.get("displayName", ""),
                    "emails": emails,
                    "company": c.get("companyName", ""),
                    "job_title": c.get("jobTitle", ""),
                    "phones": phones,
                })
            return json.dumps({"contacts": contacts, "count": len(contacts)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list contacts: {e}"})


if __name__ == "__main__":
    M365MCPServer().start()
