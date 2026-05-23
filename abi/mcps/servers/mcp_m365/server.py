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
    m365_sharepoint — List SharePoint sites, drives, browse libraries
    m365_onenote   — List notebooks, sections, read/create pages
    m365_excel     — List worksheets, read/update cell ranges
    m365_planner   — List plans, tasks, create/update tasks
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
    "https://graph.microsoft.com/Sites.Read.All",
    "https://graph.microsoft.com/Notes.Read",
    "https://graph.microsoft.com/Notes.ReadWrite",
    "https://graph.microsoft.com/Group.Read.All",
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
            # --- SharePoint ---
            types.Tool(
                name="list_sites",
                description="List SharePoint sites the user has access to.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "search": {
                            "type": "string",
                            "description": "Search query to filter sites (optional, defaults to all).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="list_shared_drives",
                description="List all drives accessible to the user, including SharePoint document libraries.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "site_id": {
                            "type": "string",
                            "description": "SharePoint site ID to list its drives (optional, defaults to all drives).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="browse_shared_drive",
                description="Browse files and folders in a SharePoint document library or shared drive.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "drive_id": {
                            "type": "string",
                            "description": "Drive ID of the SharePoint library.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Folder path within the drive (optional, defaults to root).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max items (default: 50).",
                            "default": 50,
                        },
                    },
                    "required": ["drive_id"],
                },
            ),
            # --- OneNote ---
            types.Tool(
                name="list_notebooks",
                description="List OneNote notebooks.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="list_sections",
                description="List sections in a OneNote notebook.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "notebook_id": {
                            "type": "string",
                            "description": "Notebook ID (optional, defaults to first notebook).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="list_pages",
                description="List pages in a OneNote section or across all sections.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "section_id": {
                            "type": "string",
                            "description": "Section ID (optional, lists recent pages from all sections if omitted).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max pages (default: 25).",
                            "default": 25,
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="get_page",
                description="Read the content of a OneNote page.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "page_id": {
                            "type": "string",
                            "description": "Page ID to read.",
                        },
                    },
                    "required": ["page_id"],
                },
            ),
            types.Tool(
                name="create_page",
                description="Create a new OneNote page with HTML content.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "section_id": {
                            "type": "string",
                            "description": "Section ID (optional, creates in default section).",
                        },
                        "title": {
                            "type": "string",
                            "description": "Page title.",
                        },
                        "body_html": {
                            "type": "string",
                            "description": "Page body as HTML.",
                        },
                    },
                    "required": ["title"],
                },
            ),
            # --- Excel ---
            types.Tool(
                name="list_worksheets",
                description="List worksheets in an Excel workbook (by Drive item ID or path).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "Drive item ID of the Excel file.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Path to the Excel file in OneDrive (alternative to file_id).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="get_worksheet_data",
                description="Read data from an Excel worksheet (entire used range or specific range).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "Drive item ID of the Excel file."},
                        "path": {"type": "string", "description": "Path to the Excel file in OneDrive (alternative to file_id)."},
                        "worksheet": {"type": "string", "description": "Worksheet name (default: first worksheet)."},
                        "range": {"type": "string", "description": "Cell range (e.g., 'A1:D10'). Omit for entire used range."},
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="update_worksheet_range",
                description="Write data to a range in an Excel worksheet.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "Drive item ID of the Excel file."},
                        "path": {"type": "string", "description": "Path to the Excel file in OneDrive (alternative to file_id)."},
                        "worksheet": {"type": "string", "description": "Worksheet name (default: first worksheet)."},
                        "range": {"type": "string", "description": "Target cell range (e.g., 'A1:C3')."},
                        "values": {
                            "type": "array",
                            "description": "2D array of values (rows of columns). E.g., [[1,2,3],[4,5,6]].",
                            "items": {"type": "array", "items": {}},
                        },
                    },
                    "required": ["range", "values"],
                },
            ),
            # --- Planner ---
            types.Tool(
                name="list_plans",
                description="List Microsoft Planner plans the user has access to.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="list_plan_tasks",
                description="List tasks in a Planner plan.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "Planner plan ID."},
                    },
                    "required": ["plan_id"],
                },
            ),
            types.Tool(
                name="create_planner_task",
                description="Create a new task in a Planner plan.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "Planner plan ID."},
                        "title": {"type": "string", "description": "Task title."},
                        "bucket_id": {"type": "string", "description": "Bucket ID (optional)."},
                        "due_date": {"type": "string", "description": "Due date YYYY-MM-DD (optional)."},
                        "priority": {"type": "integer", "description": "Priority 1 (urgent) to 10 (low). Default: 5."},
                    },
                    "required": ["plan_id", "title"],
                },
            ),
            types.Tool(
                name="update_planner_task",
                description="Update a Planner task (status, title, due date, etc.).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Planner task ID."},
                        "title": {"type": "string", "description": "New title."},
                        "percent_complete": {"type": "integer", "description": "Completion percentage (0-100). 100 = completed."},
                        "due_date": {"type": "string", "description": "Due date YYYY-MM-DD."},
                        "priority": {"type": "integer", "description": "Priority 1-10."},
                    },
                    "required": ["task_id"],
                },
            ),
            types.Tool(
                name="list_plan_buckets",
                description="List buckets (columns/swimlanes) in a Planner plan.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "Planner plan ID."},
                    },
                    "required": ["plan_id"],
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
            # SharePoint
            "list_sites": self._list_sites,
            "list_shared_drives": self._list_shared_drives,
            "browse_shared_drive": self._browse_shared_drive,
            # OneNote
            "list_notebooks": self._list_notebooks,
            "list_sections": self._list_sections,
            "list_pages": self._list_pages,
            "get_page": self._get_page,
            "create_page": self._create_page,
            # Excel
            "list_worksheets": self._list_worksheets,
            "get_worksheet_data": self._get_worksheet_data,
            "update_worksheet_range": self._update_worksheet_range,
            # Planner
            "list_plans": self._list_plans,
            "list_plan_tasks": self._list_plan_tasks,
            "create_planner_task": self._create_planner_task,
            "update_planner_task": self._update_planner_task,
            "list_plan_buckets": self._list_plan_buckets,
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
    # SharePoint
    # =========================================================================

    async def _list_sites(self, args: Dict[str, Any]) -> str:
        search = args.get("search", "").strip()
        try:
            import urllib.parse
            if search:
                data = self._graph_get("/sites", {"search": urllib.parse.quote(search, safe='')})
            else:
                data = self._graph_get("/sites", {"search": "*"})
            sites = []
            for s in data.get("value", []):
                sites.append({
                    "id": s.get("id", ""),
                    "name": s.get("displayName", ""),
                    "url": s.get("webUrl", ""),
                    "description": s.get("description", "")[:200],
                })
            return json.dumps({"sites": sites, "count": len(sites)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list SharePoint sites: {e}"})

    async def _list_shared_drives(self, args: Dict[str, Any]) -> str:
        site_id = args.get("site_id", "").strip()
        try:
            import urllib.parse
            if site_id:
                # List drives for a specific SharePoint site
                data = self._graph_get(f"/sites/{site_id}/drives")
            else:
                # List all drives accessible to the user (OneDrive + SharePoint libraries)
                data = self._graph_get("/me/drives")
            drives = []
            for d in data.get("value", []):
                drives.append({
                    "id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "type": d.get("driveType", ""),  # personal, business, documentLibrary
                    "url": d.get("webUrl", ""),
                    "owner": d.get("owner", {}).get("user", {}).get("displayName", ""),
                })
            return json.dumps({"drives": drives, "count": len(drives)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list drives: {e}"})

    async def _browse_shared_drive(self, args: Dict[str, Any]) -> str:
        drive_id = args.get("drive_id", "").strip()
        if not drive_id:
            return json.dumps({"error": "drive_id is required. Use list_shared_drives to find drive IDs."})
        path = args.get("path", "").strip()
        limit = min(args.get("limit", 50), 200)
        try:
            import urllib.parse
            if path:
                endpoint = f"/drives/{drive_id}/root:/{urllib.parse.quote(path, safe='')}:/children"
            else:
                endpoint = f"/drives/{drive_id}/root/children"
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
            return json.dumps({"error": f"Failed to browse shared drive: {e}"})

    # =========================================================================
    # OneNote
    # =========================================================================

    async def _list_notebooks(self, args: Dict[str, Any]) -> str:
        try:
            data = self._graph_get("/me/onenote/notebooks")
            notebooks = []
            for nb in data.get("value", []):
                notebooks.append({
                    "id": nb.get("id", ""),
                    "name": nb.get("displayName", ""),
                    "created": nb.get("createdDateTime", ""),
                    "last_modified": nb.get("lastModifiedDateTime", ""),
                    "url": nb.get("links", {}).get("oneNoteWebUrl", {}).get("href", ""),
                })
            return json.dumps({"notebooks": notebooks, "count": len(notebooks)})
        except RuntimeError as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": "OneNote not available. The tenant may not have OneNote provisioned or Notes.Read permission not granted."})
            return json.dumps({"error": f"Failed to list notebooks: {e}"})

    async def _list_sections(self, args: Dict[str, Any]) -> str:
        notebook_id = args.get("notebook_id", "").strip()
        try:
            if notebook_id:
                data = self._graph_get(f"/me/onenote/notebooks/{notebook_id}/sections")
            else:
                # Default to first notebook
                nb_data = self._graph_get("/me/onenote/notebooks")
                notebooks = nb_data.get("value", [])
                if not notebooks:
                    return json.dumps({"sections": [], "count": 0, "hint": "No notebooks found."})
                data = self._graph_get(f"/me/onenote/notebooks/{notebooks[0]['id']}/sections")
            sections = []
            for s in data.get("value", []):
                sections.append({
                    "id": s.get("id", ""),
                    "name": s.get("displayName", ""),
                    "pages_url": s.get("pagesUrl", ""),
                })
            return json.dumps({"sections": sections, "count": len(sections)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list sections: {e}"})

    async def _list_pages(self, args: Dict[str, Any]) -> str:
        section_id = args.get("section_id", "").strip()
        limit = min(args.get("limit", 25), 100)
        try:
            if section_id:
                data = self._graph_get(f"/me/onenote/sections/{section_id}/pages", {"$top": str(limit), "$orderby": "lastModifiedDateTime desc"})
            else:
                # No section_id — list ALL pages across all sections (better for finding readable pages)
                data = self._graph_get("/me/onenote/pages", {"$top": str(limit), "$orderby": "lastModifiedDateTime desc"})
            pages = []
            for p in data.get("value", []):
                pages.append({
                    "id": p.get("id", ""),
                    "title": p.get("title", "(untitled)"),
                    "created": p.get("createdDateTime", ""),
                    "last_modified": p.get("lastModifiedDateTime", ""),
                    "section": p.get("parentSection", {}).get("displayName", ""),
                    "self_url": p.get("links", {}).get("oneNoteClientUrl", {}).get("href", ""),
                })
            return json.dumps({"pages": pages, "count": len(pages)})
        except RuntimeError as e:
            err = str(e)
            if "SyncStateNotSupported" in err or "not supported" in err.lower():
                return json.dumps({
                    "error": "This section does not support Graph API sync. This happens with sections created in the OneNote desktop app or older sections.",
                    "hint": "Try list_pages without a section_id to see pages from all API-compatible sections. Pages created via the API are always readable.",
                })
            return json.dumps({"error": f"Failed to list pages: {e}"})

    async def _get_page(self, args: Dict[str, Any]) -> str:
        page_id = args.get("page_id", "").strip()
        if not page_id:
            return json.dumps({"error": "page_id is required."})
        try:
            import urllib.request, urllib.error
            token = self._get_token()
            if not token:
                return self._no_creds_error()
            req = urllib.request.Request(
                f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content",
                headers={"Authorization": f"Bearer {token}", "accept": "text/html"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                html_content = resp.read().decode("utf-8", errors="replace")
            return json.dumps({"page_id": page_id, "content": html_content, "content_length": len(html_content)})
        except Exception as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": f"Page {page_id} not found."})
            if "SyncStateNotSupported" in err or "not supported" in err.lower():
                return json.dumps({"error": "This page is in a section that doesn't support Graph API sync. Only pages in API-created sections are readable via the REST API."})
            return json.dumps({"error": f"Failed to get page: {e}"})

    async def _create_page(self, args: Dict[str, Any]) -> str:
        title = args.get("title", "").strip()
        if not title:
            return json.dumps({"error": "title is required."})
        body_html = args.get("body_html", "").strip()
        section_id = args.get("section_id", "").strip()
        try:
            import urllib.request, urllib.error
            token = self._get_token()
            if not token:
                return self._no_creds_error()

            # If no section specified, try to find an API-compatible section (not a sync-blocked one)
            if not section_id:
                nb_data = self._graph_get("/me/onenote/notebooks")
                for nb in nb_data.get("value", []):
                    sec_data = self._graph_get(f"/me/onenote/notebooks/{nb['id']}/sections")
                    for sec in sec_data.get("value", []):
                        section_id = sec["id"]
                        break
                    if section_id:
                        break

            page_html = f"<!DOCTYPE html><html><head><title>{title}</title></head><body>{body_html or '<p></p>'}</body></html>"
            endpoint = f"/me/onenote/sections/{section_id}/pages" if section_id else "/me/onenote/pages"
            data = page_html.encode("utf-8")
            req = urllib.request.Request(
                f"{GRAPH_BASE}{endpoint}", data=data,
                headers={"Authorization": f"Bearer {token}", "content-type": "text/html"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read())
            return json.dumps({
                "status": "created",
                "page_id": result.get("id", ""),
                "title": result.get("title", title),
                "section": result.get("parentSection", {}).get("displayName", ""),
            })
        except Exception as e:
            err = str(e)
            if "SyncStateNotSupported" in err or "not supported" in err.lower():
                return json.dumps({
                    "error": "Cannot create page in this section — it doesn't support Graph API sync.",
                    "hint": "Pages can only be created in API-compatible sections. The create_page tool will auto-pick the first compatible section if no section_id is provided.",
                })
            return json.dumps({"error": f"Failed to create page: {e}"})

    # =========================================================================
    # Excel
    # =========================================================================

    def _resolve_drive_item(self, file_id: str, path: str) -> str:
        """Resolve file_id or path to a drive item ID. Returns item ID or raises."""
        import urllib.parse
        if file_id:
            return file_id
        if not path:
            raise RuntimeError("Provide either file_id or path.")
        # Resolve path to item ID
        data = self._graph_get(f"/me/drive/root:/{urllib.parse.quote(path, safe='')}")
        item_id = data.get("id", "")
        if not item_id:
            raise RuntimeError(f"File not found at path: {path}")
        return item_id

    async def _list_worksheets(self, args: Dict[str, Any]) -> str:
        file_id = args.get("file_id", "").strip()
        path = args.get("path", "").strip()
        if not file_id and not path:
            return json.dumps({"error": "Provide either file_id or path to an Excel file."})
        try:
            item_id = self._resolve_drive_item(file_id, path)
            data = self._graph_get(f"/me/drive/items/{item_id}/workbook/worksheets")
            sheets = []
            for ws in data.get("value", []):
                sheets.append({
                    "id": ws.get("id", ""),
                    "name": ws.get("name", ""),
                    "position": ws.get("position", 0),
                    "visibility": ws.get("visibility", ""),
                })
            return json.dumps({"worksheets": sheets, "count": len(sheets)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list worksheets: {e}"})

    async def _get_worksheet_data(self, args: Dict[str, Any]) -> str:
        file_id = args.get("file_id", "").strip()
        path = args.get("path", "").strip()
        worksheet = args.get("worksheet", "").strip()
        cell_range = args.get("range", "").strip()
        if not file_id and not path:
            return json.dumps({"error": "Provide either file_id or path to an Excel file."})
        try:
            import urllib.parse
            item_id = self._resolve_drive_item(file_id, path)
            # Default to first worksheet if not specified
            if not worksheet:
                ws_data = self._graph_get(f"/me/drive/items/{item_id}/workbook/worksheets")
                sheets = ws_data.get("value", [])
                if not sheets:
                    return json.dumps({"error": "No worksheets found in the workbook."})
                worksheet = sheets[0]["name"]

            ws_encoded = urllib.parse.quote(worksheet, safe='')
            if cell_range:
                endpoint = f"/me/drive/items/{item_id}/workbook/worksheets/{ws_encoded}/range(address='{urllib.parse.quote(cell_range, safe='')}')"
            else:
                endpoint = f"/me/drive/items/{item_id}/workbook/worksheets/{ws_encoded}/usedRange"

            data = self._graph_get(endpoint)
            values = data.get("values", [])
            text = data.get("text", values)
            return json.dumps({
                "worksheet": worksheet,
                "range": cell_range or "usedRange",
                "rows": len(values),
                "columns": len(values[0]) if values else 0,
                "values": values,
                "text": text,
            })
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to read worksheet data: {e}"})

    async def _update_worksheet_range(self, args: Dict[str, Any]) -> str:
        file_id = args.get("file_id", "").strip()
        path = args.get("path", "").strip()
        worksheet = args.get("worksheet", "").strip()
        cell_range = args.get("range", "").strip()
        values = args.get("values", [])
        if not cell_range:
            return json.dumps({"error": "range is required (e.g., 'A1:C3')."})
        if not values:
            return json.dumps({"error": "values is required (2D array, e.g., [[1,2,3],[4,5,6]])."})
        if not file_id and not path:
            return json.dumps({"error": "Provide either file_id or path to an Excel file."})
        try:
            import urllib.parse
            item_id = self._resolve_drive_item(file_id, path)
            if not worksheet:
                ws_data = self._graph_get(f"/me/drive/items/{item_id}/workbook/worksheets")
                sheets = ws_data.get("value", [])
                if not sheets:
                    return json.dumps({"error": "No worksheets found in the workbook."})
                worksheet = sheets[0]["name"]

            ws_encoded = urllib.parse.quote(worksheet, safe='')
            endpoint = f"/me/drive/items/{item_id}/workbook/worksheets/{ws_encoded}/range(address='{urllib.parse.quote(cell_range, safe='')}')"
            result = self._graph_patch(endpoint, {"values": values})
            return json.dumps({
                "status": "updated",
                "worksheet": worksheet,
                "range": cell_range,
            })
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to update worksheet: {e}"})

    # =========================================================================
    # Planner
    # =========================================================================

    async def _list_plans(self, args: Dict[str, Any]) -> str:
        try:
            # Get plans from groups the user belongs to
            user_data = self._graph_get("/me")
            user_id = user_data.get("id", "")
            # List plans the user has access to via /me/planner/plans (not available)
            # Planner plans are accessed via groups
            groups_data = self._graph_get("/me/memberOf", {"$select": "id,displayName,groupTypes"})
            groups = [g for g in groups_data.get("value", []) if "Unified" in g.get("groupTypes", [])]
            all_plans = []
            for group in groups[:20]:  # Limit to avoid too many API calls
                try:
                    plans_data = self._graph_get(f"/groups/{group['id']}/planner/plans")
                    for p in plans_data.get("value", []):
                        all_plans.append({
                            "id": p.get("id", ""),
                            "title": p.get("title", ""),
                            "owner_group_id": group["id"],
                            "owner_group_name": group.get("displayName", ""),
                            "created": p.get("createdDateTime", ""),
                        })
                except RuntimeError:
                    continue  # Group may not have Planner
            return json.dumps({"plans": all_plans, "count": len(all_plans)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list Planner plans: {e}"})

    async def _list_plan_tasks(self, args: Dict[str, Any]) -> str:
        plan_id = args.get("plan_id", "").strip()
        if not plan_id:
            return json.dumps({"error": "plan_id is required. Use list_plans to find plan IDs."})
        try:
            data = self._graph_get(f"/planner/plans/{plan_id}/tasks")
            tasks = []
            for t in data.get("value", []):
                tasks.append({
                    "id": t.get("id", ""),
                    "title": t.get("title", ""),
                    "status": t.get("status", ""),
                    "percent_complete": t.get("percentComplete", 0),
                    "priority": t.get("priority", 5),
                    "bucket_id": t.get("bucketId", ""),
                    "due_date": t.get("dueDateTime", ""),
                    "created": t.get("createdDateTime", ""),
                    "etag": t.get("@odata.etag", ""),
                })
            return json.dumps({"tasks": tasks, "count": len(tasks)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list plan tasks: {e}"})

    async def _create_planner_task(self, args: Dict[str, Any]) -> str:
        plan_id = args.get("plan_id", "").strip()
        title = args.get("title", "").strip()
        if not plan_id or not title:
            return json.dumps({"error": "plan_id and title are required."})
        try:
            payload = {"planId": plan_id, "title": title}
            if args.get("bucket_id"):
                payload["bucketId"] = args["bucket_id"]
            if args.get("due_date"):
                from datetime import datetime
                try:
                    dt = datetime.strptime(args["due_date"], "%Y-%m-%d")
                    payload["dueDateTime"] = {"dateTime": dt.isoformat(), "timeZone": "UTC"}
                except ValueError:
                    return json.dumps({"error": f"Invalid due_date '{args['due_date']}'. Use YYYY-MM-DD."})
            if args.get("priority") is not None:
                payload["priority"] = args["priority"]
            result = self._graph_post("/planner/tasks", payload)
            return json.dumps({
                "status": "created",
                "task_id": result.get("id", ""),
                "title": result.get("title", title),
            })
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to create Planner task: {e}"})

    async def _update_planner_task(self, args: Dict[str, Any]) -> str:
        task_id = args.get("task_id", "").strip()
        if not task_id:
            return json.dumps({"error": "task_id is required."})
        try:
            import urllib.request, urllib.error
            token = self._get_token()
            if not token:
                return self._no_creds_error()

            # First get the task to retrieve its etag (required for updates)
            task_data = self._graph_get(f"/planner/tasks/{task_id}")
            etag = task_data.get("@odata.etag", "")
            if not etag:
                return json.dumps({"error": f"Could not get etag for task {task_id}."})

            payload = {}
            if args.get("title"):
                payload["title"] = args["title"]
            if args.get("percent_complete") is not None:
                payload["percentComplete"] = args["percent_complete"]
            if args.get("due_date"):
                from datetime import datetime
                try:
                    dt = datetime.strptime(args["due_date"], "%Y-%m-%d")
                    payload["dueDateTime"] = {"dateTime": dt.isoformat(), "timeZone": "UTC"}
                except ValueError:
                    return json.dumps({"error": f"Invalid due_date '{args['due_date']}'. Use YYYY-MM-DD."})
            if args.get("priority") is not None:
                payload["priority"] = args["priority"]

            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{GRAPH_BASE}/planner/tasks/{task_id}", data=data,
                headers={"Authorization": f"Bearer {token}", "content-type": "application/json", "If-Match": etag},
                method="PATCH",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                result = json.loads(raw) if raw else {}
            return json.dumps({"status": "updated", "task_id": task_id})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 412:
                return json.dumps({"error": "Precondition failed — task was modified by another user. Fetch the latest task and retry."})
            return json.dumps({"error": f"HTTP {e.code} updating task: {body}"})
        except Exception as e:
            return json.dumps({"error": f"Failed to update Planner task: {e}"})

    async def _list_plan_buckets(self, args: Dict[str, Any]) -> str:
        plan_id = args.get("plan_id", "").strip()
        if not plan_id:
            return json.dumps({"error": "plan_id is required. Use list_plans to find plan IDs."})
        try:
            data = self._graph_get(f"/planner/plans/{plan_id}/buckets")
            buckets = []
            for b in data.get("value", []):
                buckets.append({
                    "id": b.get("id", ""),
                    "name": b.get("name", ""),
                    "order": b.get("orderHint", ""),
                })
            return json.dumps({"buckets": buckets, "count": len(buckets)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list plan buckets: {e}"})

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
                "$select": "displayName,emailAddresses,companyName,jobTitle,mobilePhone,businessPhones,homePhones",
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
                phone_list = []
                if c.get("mobilePhone"):
                    phone_list.append(c["mobilePhone"])
                phone_list.extend(c.get("businessPhones", []))
                phone_list.extend(c.get("homePhones", []))
                contacts.append({
                    "name": c.get("displayName", ""),
                    "emails": emails,
                    "company": c.get("companyName", ""),
                    "job_title": c.get("jobTitle", ""),
                    "phones": phone_list,
                })
            return json.dumps({"contacts": contacts, "count": len(contacts)})
        except RuntimeError as e:
            return json.dumps({"error": f"Failed to list contacts: {e}"})


if __name__ == "__main__":
    M365MCPServer().start()
