"""Invoice Ninja MCP Server — REST API accessor.

Tools (44 total):
    invoice_ninja_status            — Check auth status
    invoice_ninja_login             — Store API token + base URL
    invoice_ninja_logout            — Remove credentials

    Clients (5): list, get, create, update, archive
    Invoices (9): list, get, create, update, delete, send, mark_paid, cancel
    Payments (5): list, get, create, update, delete
    Products (5): list, get, create, update, delete
    Recurring (5): list, get, create, update, delete
    Quotes (6): list, get, create, update, delete, quote_to_invoice
    Tasks (5): list, get, create, update, delete
    Projects (5): list, get, create, update, delete

Auth: API token from Invoice Ninja settings.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from abi.mcps.base import ABIMCPServer
from mcp import types


class InvoiceNinjaMCPServer(ABIMCPServer):
    SERVICE_NAME = "invoice_ninja"

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_token": {"type": "string", "description": "Invoice Ninja API token."},
                "base_url": {"type": "string", "description": "Invoice Ninja base URL (e.g., https://billing.example.com)."},
            },
            "required": ["api_token", "base_url"],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            # ── Clients ──────────────────────────────────────────────────
            types.Tool(name="list_clients", description="List clients.",
                inputSchema={"type": "object", "properties": {}, "required": []}),
            types.Tool(name="get_client", description="Get a client by ID.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Client ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_client", description="Create a new client.",
                inputSchema={"type": "object", "properties": {
                    "name": {"type": "string", "description": "Client display name."},
                    "email": {"type": "string", "description": "Contact email."},
                    "phone": {"type": "string", "description": "Phone number."},
                    "website": {"type": "string", "description": "Website URL."},
                    "vat_number": {"type": "string", "description": "VAT number."},
                    "address1": {"type": "string", "description": "Address line 1."},
                    "city": {"type": "string", "description": "City."},
                    "state": {"type": "string", "description": "State/region."},
                    "postal_code": {"type": "string", "description": "Postal code."},
                    "country_id": {"type": "string", "description": "Country ID (e.g., '462' for Malta)."},
                }, "required": ["name"]}),
            types.Tool(name="update_client", description="Update an existing client.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Client ID."},
                    "name": {"type": "string", "description": "New name."},
                    "email": {"type": "string", "description": "New email."},
                    "phone": {"type": "string", "description": "New phone."},
                    "website": {"type": "string", "description": "New website."},
                    "vat_number": {"type": "string", "description": "New VAT number."},
                    "address1": {"type": "string", "description": "New address."},
                    "city": {"type": "string", "description": "New city."},
                    "state": {"type": "string", "description": "New state."},
                    "postal_code": {"type": "string", "description": "New postal code."},
                    "country_id": {"type": "string", "description": "New country ID."},
                }, "required": ["id"]}),
            types.Tool(name="archive_client", description="Archive a client.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Client ID to archive."},
                }, "required": ["id"]}),
            # ── Invoices ─────────────────────────────────────────────────
            types.Tool(name="list_invoices", description="List invoices. Filter by status or client.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Filter by client ID."},
                    "status": {"type": "string", "description": "Filter by status: draft, sent, partial, paid, cancelled, overdue."},
                }, "required": []}),
            types.Tool(name="get_invoice", description="Get an invoice by ID with full details.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Invoice ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_invoice", description="Create an invoice. Requires client_id and line_items.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Client ID to invoice."},
                    "line_items": {"type": "string", "description": "JSON array of line items: [{\"product_key\":\"Service\",\"notes\":\"Desc\",\"cost\":100,\"quantity\":1,\"tax_rate\":18}]."},
                    "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)."},
                    "po_number": {"type": "string", "description": "PO/reference number."},
                    "public_notes": {"type": "string", "description": "Public notes on invoice."},
                }, "required": ["client_id", "line_items"]}),
            types.Tool(name="update_invoice", description="Update an existing invoice.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Invoice ID."},
                    "line_items": {"type": "string", "description": "New JSON array of line items."},
                    "due_date": {"type": "string", "description": "New due date (YYYY-MM-DD)."},
                    "public_notes": {"type": "string", "description": "New public notes."},
                    "po_number": {"type": "string", "description": "New PO number."},
                }, "required": ["id"]}),
            types.Tool(name="delete_invoice", description="Delete an invoice.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Invoice ID to delete."},
                }, "required": ["id"]}),
            types.Tool(name="send_invoice", description="Send an invoice via email.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Invoice ID to send."},
                }, "required": ["id"]}),
            types.Tool(name="mark_paid", description="Mark an invoice as paid.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Invoice ID."},
                    "amount": {"type": "number", "description": "Amount paid. Omit to mark full invoice as paid."},
                }, "required": ["id"]}),
            types.Tool(name="cancel_invoice", description="Cancel an invoice.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Invoice ID to cancel."},
                }, "required": ["id"]}),
            # ── Payments ─────────────────────────────────────────────────
            types.Tool(name="list_payments", description="List payments.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Filter by client ID."},
                }, "required": []}),
            types.Tool(name="get_payment", description="Get a payment by ID.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Payment ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_payment", description="Create a payment for an invoice.",
                inputSchema={"type": "object", "properties": {
                    "invoice_id": {"type": "string", "description": "Invoice ID."},
                    "amount": {"type": "number", "description": "Payment amount."},
                    "date": {"type": "string", "description": "Payment date (YYYY-MM-DD)."},
                    "transaction_reference": {"type": "string", "description": "Transaction reference."},
                }, "required": ["invoice_id", "amount"]}),
            types.Tool(name="update_payment", description="Update a payment.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Payment ID."},
                    "transaction_reference": {"type": "string", "description": "New transaction reference."},
                    "date": {"type": "string", "description": "New date (YYYY-MM-DD)."},
                }, "required": ["id"]}),
            types.Tool(name="delete_payment", description="Delete a payment.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Payment ID to delete."},
                }, "required": ["id"]}),
            # ── Products ─────────────────────────────────────────────────
            types.Tool(name="list_products", description="List products.",
                inputSchema={"type": "object", "properties": {}, "required": []}),
            types.Tool(name="get_product", description="Get a product by ID.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Product ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_product", description="Create a product.",
                inputSchema={"type": "object", "properties": {
                    "product_key": {"type": "string", "description": "Product key/SKU."},
                    "notes": {"type": "string", "description": "Product description."},
                    "cost": {"type": "number", "description": "Unit price."},
                    "tax_rate": {"type": "number", "description": "Tax rate percentage (default 18)."},
                }, "required": ["product_key", "cost"]}),
            types.Tool(name="update_product", description="Update an existing product.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Product ID."},
                    "product_key": {"type": "string", "description": "New product key/SKU."},
                    "notes": {"type": "string", "description": "New description."},
                    "cost": {"type": "number", "description": "New unit price."},
                    "tax_rate": {"type": "number", "description": "New tax rate."},
                }, "required": ["id"]}),
            types.Tool(name="delete_product", description="Delete a product.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Product ID to delete."},
                }, "required": ["id"]}),
            # ── Recurring Invoices ───────────────────────────────────────
            types.Tool(name="list_recurring", description="List recurring invoices.",
                inputSchema={"type": "object", "properties": {}, "required": []}),
            types.Tool(name="get_recurring", description="Get a recurring invoice by ID.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Recurring invoice ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_recurring", description="Create a recurring invoice.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Client ID."},
                    "line_items": {"type": "string", "description": "JSON array of line items."},
                    "frequency": {"type": "string", "description": "Frequency: daily, weekly, biweekly, monthly, quarterly, semiannually, annually (default monthly)."},
                    "next_send_date": {"type": "string", "description": "First send date (YYYY-MM-DD)."},
                    "due_date_days": {"type": "string", "description": "Days until due from send date (default '14')."},
                }, "required": ["client_id", "line_items"]}),
            types.Tool(name="update_recurring", description="Update a recurring invoice.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Recurring invoice ID."},
                    "line_items": {"type": "string", "description": "New JSON array of line items."},
                    "frequency": {"type": "string", "description": "New frequency: daily, weekly, biweekly, monthly, quarterly, semiannually, annually."},
                    "next_send_date": {"type": "string", "description": "New next send date (YYYY-MM-DD)."},
                }, "required": ["id"]}),
            types.Tool(name="delete_recurring", description="Delete a recurring invoice.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Recurring invoice ID to delete."},
                }, "required": ["id"]}),
            # ── Quotes ───────────────────────────────────────────────────
            types.Tool(name="list_quotes", description="List quotes.",
                inputSchema={"type": "object", "properties": {}, "required": []}),
            types.Tool(name="get_quote", description="Get a quote by ID with full details.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Quote ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_quote", description="Create a quote.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Client ID."},
                    "line_items": {"type": "string", "description": "JSON array of line items."},
                    "valid_until": {"type": "string", "description": "Valid until date (YYYY-MM-DD)."},
                    "public_notes": {"type": "string", "description": "Public notes."},
                }, "required": ["client_id", "line_items"]}),
            types.Tool(name="update_quote", description="Update an existing quote.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Quote ID."},
                    "line_items": {"type": "string", "description": "New JSON array of line items."},
                    "valid_until": {"type": "string", "description": "New valid until date (YYYY-MM-DD)."},
                    "public_notes": {"type": "string", "description": "New public notes."},
                }, "required": ["id"]}),
            types.Tool(name="delete_quote", description="Delete a quote.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Quote ID to delete."},
                }, "required": ["id"]}),
            types.Tool(name="quote_to_invoice", description="Convert a quote to an invoice.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Quote ID to convert."},
                }, "required": ["id"]}),
            # ── Tasks ────────────────────────────────────────────────────
            types.Tool(name="list_tasks", description="List tasks.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Filter by client ID."},
                    "project_id": {"type": "string", "description": "Filter by project ID."},
                }, "required": []}),
            types.Tool(name="get_task", description="Get a task by ID.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Task ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_task", description="Create a task.",
                inputSchema={"type": "object", "properties": {
                    "description": {"type": "string", "description": "Task description."},
                    "client_id": {"type": "string", "description": "Client ID."},
                    "project_id": {"type": "string", "description": "Project ID."},
                    "rate": {"type": "number", "description": "Hourly rate."},
                }, "required": ["description"]}),
            types.Tool(name="update_task", description="Update a task.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Task ID."},
                    "description": {"type": "string", "description": "New description."},
                    "rate": {"type": "number", "description": "New hourly rate."},
                    "project_id": {"type": "string", "description": "New project ID."},
                }, "required": ["id"]}),
            types.Tool(name="delete_task", description="Delete a task.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Task ID to delete."},
                }, "required": ["id"]}),
            # ── Projects ─────────────────────────────────────────────────
            types.Tool(name="list_projects", description="List projects.",
                inputSchema={"type": "object", "properties": {
                    "client_id": {"type": "string", "description": "Filter by client ID."},
                }, "required": []}),
            types.Tool(name="get_project", description="Get a project by ID.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Project ID."},
                }, "required": ["id"]}),
            types.Tool(name="create_project", description="Create a project.",
                inputSchema={"type": "object", "properties": {
                    "name": {"type": "string", "description": "Project name."},
                    "client_id": {"type": "string", "description": "Client ID."},
                    "budget_hours": {"type": "number", "description": "Budget in hours."},
                    "task_rate": {"type": "number", "description": "Default task hourly rate."},
                }, "required": ["name"]}),
            types.Tool(name="update_project", description="Update a project.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Project ID."},
                    "name": {"type": "string", "description": "New name."},
                    "budget_hours": {"type": "number", "description": "New budget hours."},
                    "task_rate": {"type": "number", "description": "New task rate."},
                }, "required": ["id"]}),
            types.Tool(name="delete_project", description="Delete a project.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Project ID to delete."},
                }, "required": ["id"]}),
        ]

    # =========================================================================
    # Auth
    # =========================================================================

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            import urllib.request, urllib.error
            base_url = creds.get("base_url", "").rstrip("/")
            req = urllib.request.Request(
                f"{base_url}/api/v1/products?per_page=1",
                headers={
                    "X-API-Token": creds.get("api_token", ""),
                    "Accept": "application/json",
                    "User-Agent": "Opteia-ABI-MCP/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return None
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 401:
                return {"valid": False, "message": "API token is invalid."}
            if e.code == 403:
                return {"valid": False, "message": "API token lacks permissions."}
            return {"valid": False, "message": f"HTTP {e.code}: {body}"}
        except Exception:
            return None

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        api_token = args.get("api_token", "").strip()
        base_url = args.get("base_url", "").strip()
        if not api_token or not base_url:
            return json.dumps({"error": "api_token and base_url are required."})
        self._save_creds({"api_token": api_token, "base_url": base_url.rstrip("/")})
        return json.dumps({"status": "connected", "message": "Invoice Ninja connected."})

    # =========================================================================
    # REST transport
    # =========================================================================

    def _api(self, method: str, path: str, data: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict:
        """Make an authenticated REST request to Invoice Ninja."""
        import urllib.request, urllib.error

        creds = self._require_creds()
        if not creds:
            raise RuntimeError("Not authenticated. Call invoice_ninja_login first.")

        base_url = creds["base_url"].rstrip("/")
        url = f"{base_url}/api/v1/{path.lstrip('/')}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                url += f"?{qs}"

        headers = {
            "X-API-Token": creds["api_token"],
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Opteia-ABI-MCP/1.0",
        }
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 204:
                    return {}
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code} on {method} {path}: {error_body}") from e

    def _list_all(self, path: str, params: Optional[Dict] = None, page_size: int = 100) -> list:
        """Fetch all records from a paginated Invoice Ninja endpoint."""
        all_records = []
        page = 1
        while True:
            p = dict(params or {})
            p["per_page"] = str(page_size)
            p["page"] = str(page)
            data = self._api("GET", path, params=p)
            records = data.get("data", [])
            all_records.extend(records)
            # Stop if we got fewer than page_size — last page
            if len(records) < page_size:
                break
            page += 1
            # Safety: don't fetch more than 50 pages (5000 records)
            if page > 50:
                break
        return all_records

    # =========================================================================
    # Tool dispatch
    # =========================================================================

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        dispatch = {
            # Clients
            "list_clients": self._list_clients,
            "get_client": self._get_client,
            "create_client": self._create_client,
            "update_client": self._update_client,
            "archive_client": self._archive_client,
            # Invoices
            "list_invoices": self._list_invoices,
            "get_invoice": self._get_invoice,
            "create_invoice": self._create_invoice,
            "update_invoice": self._update_invoice,
            "delete_invoice": self._delete_invoice,
            "send_invoice": self._send_invoice,
            "mark_paid": self._mark_paid,
            "cancel_invoice": self._cancel_invoice,
            # Payments
            "list_payments": self._list_payments,
            "get_payment": self._get_payment,
            "create_payment": self._create_payment,
            "update_payment": self._update_payment,
            "delete_payment": self._delete_payment,
            # Products
            "list_products": self._list_products,
            "get_product": self._get_product,
            "create_product": self._create_product,
            "update_product": self._update_product,
            "delete_product": self._delete_product,
            # Recurring
            "list_recurring": self._list_recurring,
            "get_recurring": self._get_recurring,
            "create_recurring": self._create_recurring,
            "update_recurring": self._update_recurring,
            "delete_recurring": self._delete_recurring,
            # Quotes
            "list_quotes": self._list_quotes,
            "get_quote": self._get_quote,
            "create_quote": self._create_quote,
            "update_quote": self._update_quote,
            "delete_quote": self._delete_quote,
            "quote_to_invoice": self._quote_to_invoice,
            # Tasks
            "list_tasks": self._list_tasks,
            "get_task": self._get_task,
            "create_task": self._create_task,
            "update_task": self._update_task,
            "delete_task": self._delete_task,
            # Projects
            "list_projects": self._list_projects,
            "get_project": self._get_project,
            "create_project": self._create_project,
            "update_project": self._update_project,
            "delete_project": self._delete_project,
        }
        handler = dispatch.get(name)
        if handler:
            return await handler(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    # =========================================================================
    # Clients
    # =========================================================================

    async def _list_clients(self, args: Dict[str, Any]) -> str:
        try:
            records = self._list_all("clients")
            clients = []
            for c in records:
                contact = (c.get("contacts") or [{}])[0]
                clients.append({
                    "id": c.get("id", ""),
                    "name": c.get("name", ""),
                    "email": contact.get("email", ""),
                    "phone": contact.get("phone", ""),
                    "vat_number": c.get("vat_number", ""),
                    "balance": c.get("balance", 0),
                    "paid_to_date": c.get("paid_to_date", 0),
                })
            return json.dumps({"clients": clients, "count": len(clients)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_client(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"clients/{args['id']}")
            c = data.get("data", data)
            contact = (c.get("contacts") or [{}])[0]
            return json.dumps({
                "id": c.get("id", ""), "name": c.get("name", ""),
                "email": contact.get("email", ""), "phone": contact.get("phone", ""),
                "website": c.get("website", ""), "vat_number": c.get("vat_number", ""),
                "address1": c.get("address1", ""), "city": c.get("city", ""),
                "state": c.get("state", ""), "postal_code": c.get("postal_code", ""),
                "country_id": c.get("country_id", ""),
                "balance": c.get("balance", 0), "paid_to_date": c.get("paid_to_date", 0),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_client(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "name is required."})
        try:
            payload = {"name": args["name"]}
            contact = {}
            if args.get("email"): contact["email"] = args["email"]
            if args.get("phone"): contact["phone"] = args["phone"]
            if contact: payload["contacts"] = [contact]
            for f in ["website", "vat_number", "address1", "city", "state", "postal_code", "country_id"]:
                if args.get(f): payload[f] = args[f]
            data = self._api("POST", "clients", payload)
            c = data.get("data", data)
            return json.dumps({"status": "created", "id": c.get("id", ""), "name": c.get("name", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_client(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            for f in ["name", "website", "vat_number", "address1", "city", "state", "postal_code", "country_id"]:
                if args.get(f): payload[f] = args[f]
            contact = {}
            for f in ["email", "phone"]:
                if args.get(f): contact[f] = args[f]
            if contact: payload["contacts"] = [contact]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"clients/{args['id']}", payload)
            c = data.get("data", data)
            return json.dumps({"status": "updated", "id": c.get("id", ""), "name": c.get("name", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _archive_client(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("PUT", f"clients/{args['id']}", params={"archive": "true"})
            return json.dumps({"status": "archived", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Invoices
    # =========================================================================

    async def _list_invoices(self, args: Dict[str, Any]) -> str:
        try:
            params = {}
            if args.get("client_id"): params["client_id"] = args["client_id"]
            status_map = {"draft": "2", "sent": "3", "partial": "4", "paid": "5", "cancelled": "6", "overdue": "-1"}
            if args.get("status"): params["status"] = status_map.get(args["status"].lower(), args["status"])
            records = self._list_all("invoices", params)
            invoices = []
            for inv in records:
                invoices.append({
                    "id": inv.get("id", ""), "number": inv.get("number", ""),
                    "client_id": inv.get("client_id", ""), "amount": inv.get("amount", 0),
                    "balance": inv.get("balance", 0), "status": inv.get("status_id", ""),
                    "due_date": inv.get("due_date", ""), "date": inv.get("date", ""),
                })
            return json.dumps({"invoices": invoices, "count": len(invoices)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"invoices/{args['id']}")
            inv = data.get("data", data)
            return json.dumps({
                "id": inv.get("id", ""), "number": inv.get("number", ""),
                "client_id": inv.get("client_id", ""), "amount": inv.get("amount", 0),
                "balance": inv.get("balance", 0), "status_id": inv.get("status_id", ""),
                "date": inv.get("date", ""), "due_date": inv.get("due_date", ""),
                "public_notes": inv.get("public_notes", ""), "po_number": inv.get("po_number", ""),
                "line_items": inv.get("line_items", []),
                "tax": inv.get("tax_total", 0), "total": inv.get("total_taxes", 0),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("client_id"):
            return json.dumps({"error": "client_id is required."})
        if not args.get("line_items"):
            return json.dumps({"error": "line_items is required (JSON array)."})
        try:
            line_items = json.loads(args["line_items"])
            payload = {"client_id": args["client_id"], "line_items": line_items}
            if args.get("due_date"): payload["due_date"] = args["due_date"]
            if args.get("po_number"): payload["po_number"] = args["po_number"]
            if args.get("public_notes"): payload["public_notes"] = args["public_notes"]
            data = self._api("POST", "invoices", payload)
            inv = data.get("data", data)
            return json.dumps({"status": "created", "id": inv.get("id", ""), "number": inv.get("number", ""), "amount": inv.get("amount", 0)})
        except json.JSONDecodeError:
            return json.dumps({"error": "line_items must be valid JSON array."})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("line_items"): payload["line_items"] = json.loads(args["line_items"])
            if args.get("due_date"): payload["due_date"] = args["due_date"]
            if args.get("public_notes"): payload["public_notes"] = args["public_notes"]
            if args.get("po_number"): payload["po_number"] = args["po_number"]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"invoices/{args['id']}", payload)
            inv = data.get("data", data)
            return json.dumps({"status": "updated", "id": inv.get("id", ""), "number": inv.get("number", "")})
        except json.JSONDecodeError:
            return json.dumps({"error": "line_items must be valid JSON array."})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"invoices/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _send_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("GET", f"invoices/{args['id']}/send")
            return json.dumps({"status": "sent", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _mark_paid(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            params = {"paid": "true"}
            if args.get("amount"): params["amount_paid"] = str(args["amount"])
            self._api("PUT", f"invoices/{args['id']}", params=params)
            return json.dumps({"status": "paid", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _cancel_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("PUT", f"invoices/{args['id']}", params={"cancel": "true"})
            return json.dumps({"status": "cancelled", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Payments
    # =========================================================================

    async def _list_payments(self, args: Dict[str, Any]) -> str:
        try:
            params = {}
            if args.get("client_id"): params["client_id"] = args["client_id"]
            records = self._list_all("payments", params)
            payments = []
            for p in records:
                payments.append({
                    "id": p.get("id", ""), "amount": p.get("amount", 0),
                    "date": p.get("date", ""), "transaction_reference": p.get("transaction_reference", ""),
                    "client_id": p.get("client_id", ""),
                })
            return json.dumps({"payments": payments, "count": len(payments)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_payment(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"payments/{args['id']}")
            p = data.get("data", data)
            return json.dumps({
                "id": p.get("id", ""), "amount": p.get("amount", 0),
                "date": p.get("date", ""), "transaction_reference": p.get("transaction_reference", ""),
                "client_id": p.get("client_id", ""), "invoices": p.get("invoices", []),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_payment(self, args: Dict[str, Any]) -> str:
        if not args.get("invoice_id") or args.get("amount") is None:
            return json.dumps({"error": "invoice_id and amount are required."})
        try:
            payload = {
                "invoices": [{"invoice_id": args["invoice_id"], "amount": args["amount"]}],
                "amount": args["amount"],
            }
            if args.get("date"): payload["date"] = args["date"]
            if args.get("transaction_reference"): payload["transaction_reference"] = args["transaction_reference"]
            data = self._api("POST", "payments", payload)
            p = data.get("data", data)
            return json.dumps({"status": "created", "id": p.get("id", ""), "amount": args["amount"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_payment(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("transaction_reference"): payload["transaction_reference"] = args["transaction_reference"]
            if args.get("date"): payload["date"] = args["date"]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"payments/{args['id']}", payload)
            p = data.get("data", data)
            return json.dumps({"status": "updated", "id": p.get("id", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_payment(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"payments/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Products
    # =========================================================================

    async def _list_products(self, args: Dict[str, Any]) -> str:
        try:
            records = self._list_all("products")
            products = []
            for p in records:
                products.append({
                    "id": p.get("id", ""), "product_key": p.get("product_key", ""),
                    "notes": p.get("notes", ""), "cost": p.get("cost", 0),
                    "tax_rate": p.get("tax_rate1", 0),
                })
            return json.dumps({"products": products, "count": len(products)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_product(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"products/{args['id']}")
            p = data.get("data", data)
            return json.dumps({
                "id": p.get("id", ""), "product_key": p.get("product_key", ""),
                "notes": p.get("notes", ""), "cost": p.get("cost", 0),
                "tax_name1": p.get("tax_name1", ""), "tax_rate1": p.get("tax_rate1", 0),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_product(self, args: Dict[str, Any]) -> str:
        if not args.get("product_key") or args.get("cost") is None:
            return json.dumps({"error": "product_key and cost are required."})
        try:
            payload = {"product_key": args["product_key"], "cost": args["cost"]}
            if args.get("notes"): payload["notes"] = args["notes"]
            if args.get("tax_rate") is not None:
                payload["tax_name1"] = "VAT"
                payload["tax_rate1"] = str(args["tax_rate"])
            data = self._api("POST", "products", payload)
            p = data.get("data", data)
            return json.dumps({"status": "created", "id": p.get("id", ""), "product_key": p.get("product_key", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_product(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("product_key"): payload["product_key"] = args["product_key"]
            if args.get("notes"): payload["notes"] = args["notes"]
            if args.get("cost") is not None: payload["cost"] = args["cost"]
            if args.get("tax_rate") is not None:
                payload["tax_name1"] = "VAT"
                payload["tax_rate1"] = str(args["tax_rate"])
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"products/{args['id']}", payload)
            p = data.get("data", data)
            return json.dumps({"status": "updated", "id": p.get("id", ""), "product_key": p.get("product_key", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_product(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"products/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Recurring Invoices
    # =========================================================================

    async def _list_recurring(self, args: Dict[str, Any]) -> str:
        try:
            records = self._list_all("recurring_invoices")
            invoices = []
            for inv in records:
                invoices.append({
                    "id": inv.get("id", ""), "number": inv.get("number", ""),
                    "client_id": inv.get("client_id", ""), "amount": inv.get("amount", 0),
                    "frequency": inv.get("frequency_id", ""),
                    "next_send_date": inv.get("next_send_date", ""),
                    "status": inv.get("status_id", ""),
                })
            return json.dumps({"recurring_invoices": invoices, "count": len(invoices)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_recurring(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"recurring_invoices/{args['id']}")
            inv = data.get("data", data)
            return json.dumps({
                "id": inv.get("id", ""), "number": inv.get("number", ""),
                "client_id": inv.get("client_id", ""), "amount": inv.get("amount", 0),
                "frequency": inv.get("frequency_id", ""),
                "next_send_date": inv.get("next_send_date", ""),
                "status": inv.get("status_id", ""),
                "line_items": inv.get("line_items", []),
                "due_date_days": inv.get("due_date_days", ""),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_recurring(self, args: Dict[str, Any]) -> str:
        if not args.get("client_id") or not args.get("line_items"):
            return json.dumps({"error": "client_id and line_items are required."})
        try:
            line_items = json.loads(args["line_items"])
            freq_map = {"daily": "1", "weekly": "2", "biweekly": "3", "monthly": "4",
                        "quarterly": "5", "semiannually": "6", "annually": "7"}
            payload = {
                "client_id": args["client_id"],
                "line_items": line_items,
                "frequency_id": freq_map.get(args.get("frequency", "monthly"), "4"),
            }
            if args.get("next_send_date"): payload["next_send_date"] = args["next_send_date"]
            if args.get("due_date_days"): payload["due_date_days"] = args["due_date_days"]
            data = self._api("POST", "recurring_invoices", payload)
            inv = data.get("data", data)
            return json.dumps({"status": "created", "id": inv.get("id", ""), "number": inv.get("number", ""), "amount": inv.get("amount", 0)})
        except json.JSONDecodeError:
            return json.dumps({"error": "line_items must be valid JSON array."})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_recurring(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("line_items"): payload["line_items"] = json.loads(args["line_items"])
            if args.get("frequency"):
                freq_map = {"daily": "1", "weekly": "2", "biweekly": "3", "monthly": "4",
                            "quarterly": "5", "semiannually": "6", "annually": "7"}
                payload["frequency_id"] = freq_map.get(args["frequency"], "4")
            if args.get("next_send_date"): payload["next_send_date"] = args["next_send_date"]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"recurring_invoices/{args['id']}", payload)
            inv = data.get("data", data)
            return json.dumps({"status": "updated", "id": inv.get("id", ""), "number": inv.get("number", "")})
        except json.JSONDecodeError:
            return json.dumps({"error": "line_items must be valid JSON array."})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_recurring(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"recurring_invoices/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Quotes
    # =========================================================================

    async def _list_quotes(self, args: Dict[str, Any]) -> str:
        try:
            records = self._list_all("quotes")
            quotes = []
            for q in records:
                quotes.append({
                    "id": q.get("id", ""), "number": q.get("number", ""),
                    "client_id": q.get("client_id", ""), "amount": q.get("amount", 0),
                    "status": q.get("status_id", ""), "date": q.get("date", ""),
                    "valid_until": q.get("valid_until", ""),
                })
            return json.dumps({"quotes": quotes, "count": len(quotes)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_quote(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"quotes/{args['id']}")
            q = data.get("data", data)
            return json.dumps({
                "id": q.get("id", ""), "number": q.get("number", ""),
                "client_id": q.get("client_id", ""), "amount": q.get("amount", 0),
                "status": q.get("status_id", ""), "date": q.get("date", ""),
                "valid_until": q.get("valid_until", ""),
                "public_notes": q.get("public_notes", ""),
                "line_items": q.get("line_items", []),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_quote(self, args: Dict[str, Any]) -> str:
        if not args.get("client_id") or not args.get("line_items"):
            return json.dumps({"error": "client_id and line_items are required."})
        try:
            line_items = json.loads(args["line_items"])
            payload = {"client_id": args["client_id"], "line_items": line_items}
            if args.get("valid_until"): payload["valid_until"] = args["valid_until"]
            if args.get("public_notes"): payload["public_notes"] = args["public_notes"]
            data = self._api("POST", "quotes", payload)
            q = data.get("data", data)
            return json.dumps({"status": "created", "id": q.get("id", ""), "number": q.get("number", ""), "amount": q.get("amount", 0)})
        except json.JSONDecodeError:
            return json.dumps({"error": "line_items must be valid JSON array."})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_quote(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("line_items"): payload["line_items"] = json.loads(args["line_items"])
            if args.get("valid_until"): payload["valid_until"] = args["valid_until"]
            if args.get("public_notes"): payload["public_notes"] = args["public_notes"]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"quotes/{args['id']}", payload)
            q = data.get("data", data)
            return json.dumps({"status": "updated", "id": q.get("id", ""), "number": q.get("number", "")})
        except json.JSONDecodeError:
            return json.dumps({"error": "line_items must be valid JSON array."})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_quote(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"quotes/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _quote_to_invoice(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("PUT", f"quotes/{args['id']}", params={"convert": "true"})
            inv = data.get("data", data)
            return json.dumps({"status": "converted", "id": inv.get("id", ""), "number": inv.get("number", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Tasks
    # =========================================================================

    async def _list_tasks(self, args: Dict[str, Any]) -> str:
        try:
            params = {}
            # IN API filter: project_tasks= (not project_id)
            if args.get("project_id"): params["project_tasks"] = args["project_id"]
            if args.get("client_id"): params["client_id"] = args["client_id"]
            records = self._list_all("tasks", params)
            tasks = []
            for t in records:
                tasks.append({
                    "id": t.get("id", ""), "description": t.get("description", ""),
                    "rate": t.get("rate", 0), "time_log": t.get("time_log", ""),
                    "is_running": t.get("is_running", False), "is_deleted": t.get("is_deleted", False),
                    "client_id": t.get("client_id", ""), "project_id": t.get("project_id", ""),
                })
            return json.dumps({"tasks": tasks, "count": len(tasks)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_task(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"tasks/{args['id']}")
            t = data.get("data", data)
            return json.dumps({
                "id": t.get("id", ""), "description": t.get("description", ""),
                "rate": t.get("rate", 0), "time_log": t.get("time_log", ""),
                "is_running": t.get("is_running", False), "is_deleted": t.get("is_deleted", False),
                "client_id": t.get("client_id", ""), "project_id": t.get("project_id", ""),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_task(self, args: Dict[str, Any]) -> str:
        if not args.get("description"):
            return json.dumps({"error": "description is required."})
        try:
            payload = {"description": args["description"]}
            if args.get("client_id"): payload["client_id"] = args["client_id"]
            if args.get("project_id"): payload["project_id"] = args["project_id"]
            if args.get("rate") is not None: payload["rate"] = args["rate"]
            data = self._api("POST", "tasks", payload)
            t = data.get("data", data)
            return json.dumps({"status": "created", "id": t.get("id", ""), "description": t.get("description", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_task(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("description"): payload["description"] = args["description"]
            if args.get("rate") is not None: payload["rate"] = args["rate"]
            if args.get("project_id"): payload["project_id"] = args["project_id"]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"tasks/{args['id']}", payload)
            t = data.get("data", data)
            return json.dumps({"status": "updated", "id": t.get("id", ""), "description": t.get("description", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_task(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"tasks/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Projects
    # =========================================================================

    async def _list_projects(self, args: Dict[str, Any]) -> str:
        try:
            params = {}
            if args.get("client_id"): params["client_id"] = args["client_id"]
            records = self._list_all("projects", params)
            projects = []
            for p in records:
                projects.append({
                    "id": p.get("id", ""), "name": p.get("name", ""),
                    "client_id": p.get("client_id", ""),
                    "budgeted_hours": p.get("budgeted_hours", 0), "task_rate": p.get("task_rate", 0),
                })
            return json.dumps({"projects": projects, "count": len(projects)})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _get_project(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._api("GET", f"projects/{args['id']}")
            p = data.get("data", data)
            return json.dumps({
                "id": p.get("id", ""), "name": p.get("name", ""),
                "client_id": p.get("client_id", ""),
                "budgeted_hours": p.get("budgeted_hours", 0), "task_rate": p.get("task_rate", 0),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_project(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "name is required."})
        try:
            payload = {"name": args["name"]}
            if args.get("client_id"): payload["client_id"] = args["client_id"]
            if args.get("budget_hours") is not None: payload["budgeted_hours"] = args["budget_hours"]
            if args.get("task_rate") is not None: payload["task_rate"] = args["task_rate"]
            data = self._api("POST", "projects", payload)
            p = data.get("data", data)
            return json.dumps({"status": "created", "id": p.get("id", ""), "name": p.get("name", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_project(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            payload = {}
            if args.get("name"): payload["name"] = args["name"]
            if args.get("budget_hours") is not None: payload["budgeted_hours"] = args["budget_hours"]
            if args.get("task_rate") is not None: payload["task_rate"] = args["task_rate"]
            if not payload:
                return json.dumps({"error": "Provide at least one field to update."})
            data = self._api("PUT", f"projects/{args['id']}", payload)
            p = data.get("data", data)
            return json.dumps({"status": "updated", "id": p.get("id", ""), "name": p.get("name", "")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_project(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._api("DELETE", f"projects/{args['id']}")
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})


if __name__ == "__main__":
    InvoiceNinjaMCPServer().start()
