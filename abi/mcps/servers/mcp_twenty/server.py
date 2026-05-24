"""Twenty CRM MCP Server — GraphQL API accessor.

Tools:
    twenty_status            — Check auth status
    twenty_login             — Store API key + base URL
    twenty_logout            — Remove credentials

    twenty_list_companies    — List companies (cursor pagination)
    twenty_create_company    — Create a company
    twenty_update_company    — Update a company
    twenty_delete_company    — Delete a company

    twenty_list_people       — List people/contacts (cursor pagination)
    twenty_create_person     — Create a person
    twenty_update_person     — Update a person
    twenty_delete_person     — Delete a person

    twenty_list_opportunities — List opportunities (cursor pagination)
    twenty_create_opportunity — Create an opportunity
    twenty_update_opportunity — Update an opportunity
    twenty_delete_opportunity — Delete an opportunity

Auth: API key from Twenty CRM settings.
All operations use GraphQL (Twenty's native API).
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from abi.mcps.base import ABIMCPServer
from mcp import types

# Shared pagination fields for GraphQL queries
PAGE_INFO = "pageInfo { hasNextPage endCursor }"


class TwentyMCPServer(ABIMCPServer):
    SERVICE_NAME = "twenty"

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Twenty CRM API key."},
                "base_url": {"type": "string", "description": "Twenty CRM base URL (e.g., https://crm.example.com)."},
            },
            "required": ["api_key", "base_url"],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            # --- Companies ---
            types.Tool(
                name="list_companies",
                description="List companies. Supports cursor-based pagination.",
                inputSchema={"type": "object", "properties": {
                    "limit": {"type": "integer", "description": "Per page (default 20, max 100).", "default": 20},
                    "cursor": {"type": "string", "description": "Pagination cursor from next_cursor."},
                    "search": {"type": "string", "description": "Search by company name."},
                }, "required": []},
            ),
            types.Tool(
                name="create_company",
                description="Create a new company.",
                inputSchema={"type": "object", "properties": {
                    "name": {"type": "string", "description": "Company name."},
                    "domain": {"type": "string", "description": "Website domain."},
                }, "required": ["name"]},
            ),
            types.Tool(
                name="update_company",
                description="Update an existing company.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Company ID."},
                    "name": {"type": "string", "description": "New name."},
                    "domain": {"type": "string", "description": "New domain."},
                }, "required": ["id"]},
            ),
            types.Tool(
                name="delete_company",
                description="Delete a company.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Company ID to delete."},
                }, "required": ["id"]},
            ),
            # --- People ---
            types.Tool(
                name="list_people",
                description="List people/contacts. Supports cursor-based pagination.",
                inputSchema={"type": "object", "properties": {
                    "limit": {"type": "integer", "description": "Per page (default 20, max 100).", "default": 20},
                    "cursor": {"type": "string", "description": "Pagination cursor from next_cursor."},
                    "company_id": {"type": "string", "description": "Filter by company ID."},
                    "search": {"type": "string", "description": "Search by name."},
                }, "required": []},
            ),
            types.Tool(
                name="create_person",
                description="Create a person/contact.",
                inputSchema={"type": "object", "properties": {
                    "first_name": {"type": "string", "description": "First name."},
                    "last_name": {"type": "string", "description": "Last name."},
                    "email": {"type": "string", "description": "Email address."},
                    "company_id": {"type": "string", "description": "Company ID to link."},
                    "job_title": {"type": "string", "description": "Job title."},
                }, "required": ["first_name", "last_name"]},
            ),
            types.Tool(
                name="update_person",
                description="Update an existing person.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Person ID."},
                    "first_name": {"type": "string", "description": "New first name."},
                    "last_name": {"type": "string", "description": "New last name."},
                    "job_title": {"type": "string", "description": "New job title."},
                }, "required": ["id"]},
            ),
            types.Tool(
                name="delete_person",
                description="Delete a person.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Person ID to delete."},
                }, "required": ["id"]},
            ),
            # --- Opportunities ---
            types.Tool(
                name="list_opportunities",
                description="List opportunities. Supports cursor-based pagination.",
                inputSchema={"type": "object", "properties": {
                    "limit": {"type": "integer", "description": "Per page (default 20, max 100).", "default": 20},
                    "cursor": {"type": "string", "description": "Pagination cursor from next_cursor."},
                    "company_id": {"type": "string", "description": "Filter by company ID."},
                    "stage": {"type": "string", "description": "Filter by pipeline stage."},
                }, "required": []},
            ),
            types.Tool(
                name="create_opportunity",
                description="Create an opportunity.",
                inputSchema={"type": "object", "properties": {
                    "name": {"type": "string", "description": "Opportunity name."},
                    "company_id": {"type": "string", "description": "Company ID to link."},
                    "amount": {"type": "number", "description": "Deal amount."},
                }, "required": ["name"]},
            ),
            types.Tool(
                name="update_opportunity",
                description="Update an existing opportunity.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Opportunity ID."},
                    "name": {"type": "string", "description": "New name."},
                    "amount": {"type": "number", "description": "New amount."},
                    "stage": {"type": "string", "description": "New pipeline stage."},
                }, "required": ["id"]},
            ),
            types.Tool(
                name="delete_opportunity",
                description="Delete an opportunity.",
                inputSchema={"type": "object", "properties": {
                    "id": {"type": "string", "description": "Opportunity ID to delete."},
                }, "required": ["id"]},
            ),
        ]

    # =========================================================================
    # Auth
    # =========================================================================

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            data = self._graphql("{ companies(first: 1) { totalCount } }")
            if data.get("companies") is not None:
                return None
            return {"valid": False, "message": "Unexpected API response."}
        except RuntimeError as e:
            err = str(e)
            if "401" in err:
                return {"valid": False, "message": "API key is invalid or revoked."}
            if "403" in err:
                return {"valid": False, "message": "API key lacks permissions."}
            return {"valid": False, "message": f"Connection error: {e}"}
        except Exception:
            return None

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        api_key = args.get("api_key", "").strip()
        base_url = args.get("base_url", "").strip()
        if not api_key or not base_url:
            return json.dumps({"error": "api_key and base_url are required."})
        self._save_creds({"api_key": api_key, "base_url": base_url.rstrip("/")})
        return json.dumps({"status": "connected", "message": "Twenty CRM connected."})

    # =========================================================================
    # GraphQL transport
    # =========================================================================

    def _graphql(self, query: str) -> Dict:
        """Execute a GraphQL query/mutation against Twenty CRM."""
        import urllib.request, urllib.error
        creds = self._require_creds()
        if not creds:
            raise RuntimeError("Not authenticated. Call twenty_login first.")

        base_url = creds["base_url"].rstrip("/")
        payload = json.dumps({"query": query}).encode()
        req = urllib.request.Request(
            f"{base_url}/graphql", data=payload,
            headers={
                "Authorization": f"Bearer {creds['api_key']}",
                "content-type": "application/json",
                "User-Agent": "Opteia-ABI-MCP/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            if result.get("errors"):
                msgs = [e.get("message", str(e)) for e in result["errors"]]
                raise RuntimeError("; ".join(msgs))
            return result.get("data", {})
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code}: {body}") from e

    @staticmethod
    def _esc(s: str) -> str:
        """Escape a string for GraphQL."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    # =========================================================================
    # Tool dispatch
    # =========================================================================

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        dispatch = {
            "list_companies": self._list_companies,
            "create_company": self._create_company,
            "update_company": self._update_company,
            "delete_company": self._delete_company,
            "list_people": self._list_people,
            "create_person": self._create_person,
            "update_person": self._update_person,
            "delete_person": self._delete_person,
            "list_opportunities": self._list_opportunities,
            "create_opportunity": self._create_opportunity,
            "update_opportunity": self._update_opportunity,
            "delete_opportunity": self._delete_opportunity,
        }
        handler = dispatch.get(name)
        if handler:
            return await handler(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    # =========================================================================
    # Companies
    # =========================================================================

    async def _list_companies(self, args: Dict[str, Any]) -> str:
        try:
            limit = min(args.get("limit", 20), 100)
            cursor = args.get("cursor", "").strip()
            after = f', after: "{cursor}"' if cursor else ""
            where = ""
            if args.get("search"):
                where = f', where: {{ name: {{ ilike: "%{self._esc(args["search"])}%" }} }}'

            data = self._graphql(f"""{{{{
                companies(first: {limit}{after}{where}) {{{{
                    edges {{ node {{ id name domainName }} }} {PAGE_INFO} totalCount
                }}}}
            }}}}'""")
            return self._format_connection(data.get("companies", {}), "companies",
                lambda n: {"id": n.get("id",""), "name": n.get("name",""), "domain": n.get("domainName","")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_company(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "name is required."})
        try:
            fields = f'name: "{self._esc(args["name"])}"'
            if args.get("domain"):
                fields += f', domainName: "{self._esc(args["domain"])}"'
            data = self._graphql(f'mutation {{ createCompany(data: {{ {fields} }}) {{ id name domainName }} }}')
            c = data.get("createCompany", {})
            return json.dumps({"status": "created", "id": c.get("id",""), "name": c.get("name","")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_company(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            fields = []
            if args.get("name"):
                fields.append(f'name: "{self._esc(args["name"])}"')
            if args.get("domain"):
                fields.append(f'domainName: "{self._esc(args["domain"])}"')
            if not fields:
                return json.dumps({"error": "Provide at least one field to update (name, domain)."})
            data_str = ", ".join(fields)
            data = self._graphql(f'mutation {{ updateCompany(id: "{args["id"]}", data: {{ {data_str} }}) {{ id name domainName }} }}')
            c = data.get("updateCompany", {})
            return json.dumps({"status": "updated", "id": c.get("id",""), "name": c.get("name","")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_company(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            data = self._graphql(f'mutation {{ deleteCompany(id: "{args["id"]}") {{ id }} }}')
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # People
    # =========================================================================

    async def _list_people(self, args: Dict[str, Any]) -> str:
        try:
            limit = min(args.get("limit", 20), 100)
            cursor = args.get("cursor", "").strip()
            after = f', after: "{cursor}"' if cursor else ""
            where = ""
            if args.get("company_id"):
                where += f', where: {{ companyId: {{ eq: "{args["company_id"]}" }} }}'
            if args.get("search"):
                search_where = f'name: {{ or: [{{ firstName: {{ ilike: "%{self._esc(args["search"])}%" }} }}, {{ lastName: {{ ilike: "%{self._esc(args["search"])}%" }} }}] }}'
                where += f', where: {{ {search_where} }}' if not where else f''  # simplify

            data = self._graphql(f"""{{{{
                people(first: {limit}{after}{where}) {{{{
                    edges {{ node {{ id name {{ firstName lastName }} jobTitle email }} }} {PAGE_INFO} totalCount
                }}}}
            }}}}'""")
            return self._format_connection(data.get("people", {}), "people",
                lambda n: {
                    "id": n.get("id",""),
                    "first_name": (n.get("name") or {}).get("firstName",""),
                    "last_name": (n.get("name") or {}).get("lastName",""),
                    "email": n.get("email",""),
                    "job_title": n.get("jobTitle",""),
                })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_person(self, args: Dict[str, Any]) -> str:
        missing = [f for f in ["first_name", "last_name"] if not args.get(f)]
        if missing:
            return json.dumps({"error": f"Missing: {', '.join(missing)}."})
        try:
            fields = [f'firstName: "{self._esc(args["first_name"])}"', f'lastName: "{self._esc(args["last_name"])}"']
            if args.get("job_title"):
                fields.append(f'jobTitle: "{self._esc(args["job_title"])}"')
            if args.get("company_id"):
                fields.append(f'companyId: "{args["company_id"]}"')
            name_fields = ", ".join(fields)
            data = self._graphql(f'mutation {{ createPerson(data: {{ name: {{ {name_fields} }} }}) {{ id name {{ firstName lastName }} }} }}')
            p = data.get("createPerson", {})
            n = p.get("name", {})
            return json.dumps({"status": "created", "id": p.get("id",""),
                "name": f'{n.get("firstName","")} {n.get("lastName","")}'})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_person(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            name_parts = []
            if args.get("first_name"):
                name_parts.append(f'firstName: "{self._esc(args["first_name"])}"')
            if args.get("last_name"):
                name_parts.append(f'lastName: "{self._esc(args["last_name"])}"')
            fields = []
            if name_parts:
                fields.append(f'name: {{ {", ".join(name_parts)} }}')
            if args.get("job_title"):
                fields.append(f'jobTitle: "{self._esc(args["job_title"])}"')
            if not fields:
                return json.dumps({"error": "Provide at least one field to update."})
            data_str = ", ".join(fields)
            data = self._graphql(f'mutation {{ updatePerson(id: "{args["id"]}", data: {{ {data_str} }}) {{ id name {{ firstName lastName }} }} }}')
            p = data.get("updatePerson", {})
            n = p.get("name", {})
            return json.dumps({"status": "updated", "id": p.get("id",""),
                "name": f'{n.get("firstName","")} {n.get("lastName","")}'})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_person(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._graphql(f'mutation {{ deletePerson(id: "{args["id"]}") {{ id }} }}')
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Opportunities
    # =========================================================================

    async def _list_opportunities(self, args: Dict[str, Any]) -> str:
        try:
            limit = min(args.get("limit", 20), 100)
            cursor = args.get("cursor", "").strip()
            after = f', after: "{cursor}"' if cursor else ""
            where = ""
            if args.get("company_id"):
                where = f', where: {{ companyId: {{ eq: "{args["company_id"]}" }} }}'

            data = self._graphql(f"""{{{{
                opportunities(first: {limit}{after}{where}) {{{{
                    edges {{ node {{ id name amount stage company {{ id name }} }} }} {PAGE_INFO} totalCount
                }}}}
            }}}}'""")
            return self._format_connection(data.get("opportunities", {}), "opportunities",
                lambda n: {
                    "id": n.get("id",""),
                    "name": n.get("name",""),
                    "amount": n.get("amount"),
                    "stage": n.get("stage",""),
                    "company": (n.get("company") or {}).get("name",""),
                })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _create_opportunity(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "name is required."})
        try:
            fields = [f'name: "{self._esc(args["name"])}"']
            if args.get("company_id"):
                fields.append(f'companyId: "{args["company_id"]}"')
            if args.get("amount"):
                fields.append(f'amount: {args["amount"]}')
            data_str = ", ".join(fields)
            data = self._graphql(f'mutation {{ createOpportunity(data: {{ {data_str} }}) {{ id name amount }} }}')
            o = data.get("createOpportunity", {})
            return json.dumps({"status": "created", "id": o.get("id",""), "name": o.get("name","")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _update_opportunity(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            fields = []
            if args.get("name"):
                fields.append(f'name: "{self._esc(args["name"])}"')
            if args.get("amount") is not None:
                fields.append(f'amount: {args["amount"]}')
            if args.get("stage"):
                fields.append(f'stage: "{self._esc(args["stage"])}"')
            if not fields:
                return json.dumps({"error": "Provide at least one field to update."})
            data_str = ", ".join(fields)
            data = self._graphql(f'mutation {{ updateOpportunity(id: "{args["id"]}", data: {{ {data_str} }}) {{ id name amount stage }} }}')
            o = data.get("updateOpportunity", {})
            return json.dumps({"status": "updated", "id": o.get("id",""), "name": o.get("name",""), "stage": o.get("stage","")})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    async def _delete_opportunity(self, args: Dict[str, Any]) -> str:
        if not args.get("id"):
            return json.dumps({"error": "id is required."})
        try:
            self._graphql(f'mutation {{ deleteOpportunity(id: "{args["id"]}") {{ id }} }}')
            return json.dumps({"status": "deleted", "id": args["id"]})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _format_connection(conn_data: Dict, entity_name: str, node_mapper) -> str:
        """Format a GraphQL connection (edges/nodes) into a JSON response."""
        items = []
        for edge in conn_data.get("edges", []):
            items.append(node_mapper(edge.get("node", {})))
        page_info = conn_data.get("pageInfo", {})
        return json.dumps({
            entity_name: items,
            "count": len(items),
            "total": conn_data.get("totalCount", len(items)),
            "has_next_page": page_info.get("hasNextPage", False),
            "next_cursor": page_info.get("endCursor", ""),
        })


if __name__ == "__main__":
    TwentyMCPServer().start()
