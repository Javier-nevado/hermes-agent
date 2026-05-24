"""Twenty CRM MCP Server — GraphQL API accessor.

Tools:
    twenty_status            — Check auth status
    twenty_login             — Store API key + base URL
    twenty_logout            — Remove credentials
    twenty_list_companies    — List companies (cursor pagination)
    twenty_create_company    — Create a new company
    twenty_create_person     — Create a person/contact
    twenty_create_opportunity — Create an opportunity

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


class TwentyMCPServer(ABIMCPServer):
    SERVICE_NAME = "twenty"

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "Twenty CRM API key.",
                },
                "base_url": {
                    "type": "string",
                    "description": "Twenty CRM base URL (e.g., https://crm.example.com).",
                },
            },
            "required": ["api_key", "base_url"],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            types.Tool(
                name="list_companies",
                description="List companies from the CRM. Supports cursor-based pagination.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of companies per page (default: 20, max: 100).",
                            "default": 20,
                        },
                        "cursor": {
                            "type": "string",
                            "description": "Pagination cursor from previous response (next_cursor).",
                        },
                        "search": {
                            "type": "string",
                            "description": "Search term for company name.",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="create_company",
                description="Create a new company in the CRM.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Company name."},
                        "domain": {"type": "string", "description": "Company website domain."},
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="create_person",
                description="Create a person/contact in the CRM.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "first_name": {"type": "string", "description": "First name."},
                        "last_name": {"type": "string", "description": "Last name."},
                        "email": {"type": "string", "description": "Email address."},
                        "company_id": {"type": "string", "description": "ID of the company to link."},
                        "job_title": {"type": "string", "description": "Job title."},
                    },
                    "required": ["first_name", "last_name"],
                },
            ),
            types.Tool(
                name="create_opportunity",
                description="Create an opportunity in the CRM.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Opportunity name."},
                        "company_id": {"type": "string", "description": "Company ID to link."},
                        "amount": {"type": "number", "description": "Deal amount."},
                    },
                    "required": ["name"],
                },
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
                return {"valid": False, "message": "API key is invalid or revoked. Get a new key from Twenty CRM Settings > API."}
            if "403" in err:
                return {"valid": False, "message": "API key does not have permission. Check key permissions."}
            if "SSL" in err or "certificate" in err:
                return {"valid": False, "message": f"SSL/TLS error connecting to CRM. Check the base_url. Details: {e}"}
            if "Connection refused" in err or "Name or service not known" in err:
                return {"valid": False, "message": f"Cannot connect to CRM. Check the base_url. Details: {e}"}
            return {"valid": False, "message": f"Validation error: {e}"}
        except Exception:
            return None  # Assume valid if unexpected error

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

    def _graphql(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query/mutation against Twenty CRM."""
        import urllib.request, urllib.error
        creds = self._require_creds()
        if not creds:
            raise RuntimeError("Not authenticated. Call twenty_login first.")

        base_url = creds["base_url"].rstrip("/")
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{base_url}/graphql", data=data,
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

    # =========================================================================
    # Tool dispatch
    # =========================================================================

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        dispatch = {
            "list_companies": self._list_companies,
            "create_company": self._create_company,
            "create_person": self._create_person,
            "create_opportunity": self._create_opportunity,
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
            after_clause = f', after: "{cursor}"' if cursor else ""

            # Search filter via where clause
            where_clause = ""
            if args.get("search"):
                import urllib.parse
                search = args["search"].replace('"', '\\"')
                where_clause = f', where: {{ name: {{ ilike: "%{search}%" }} }}'

            query = f"""{{ companies(first: {limit}{after_clause}{where_clause}) {{
                edges {{ node {{ id name domainName }} }}
                pageInfo {{ hasNextPage endCursor }}
                totalCount
            }} }}"""
            data = self._graphql(query)
            comp_data = data.get("companies", {})
            companies = []
            for edge in comp_data.get("edges", []):
                node = edge.get("node", {})
                companies.append({
                    "id": node.get("id", ""),
                    "name": node.get("name", ""),
                    "domain": node.get("domainName", ""),
                })
            page_info = comp_data.get("pageInfo", {})
            return json.dumps({
                "companies": companies,
                "count": len(companies),
                "total": comp_data.get("totalCount", len(companies)),
                "has_next_page": page_info.get("hasNextPage", False),
                "next_cursor": page_info.get("endCursor", ""),
            })
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"Failed to list companies: {e}"})

    async def _create_company(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "Company name is required."})
        try:
            name = args["name"].replace('"', '\\"')
            domain = args.get("domain", "").replace('"', '\\"')
            domain_field = f', domainName: "{domain}"' if domain else ""

            query = f"""mutation {{
                createCompany(data: {{ name: "{name}"{domain_field} }}) {{
                    id name domainName
                }}
            }}"""
            data = self._graphql(query)
            company = data.get("createCompany", {})
            return json.dumps({
                "status": "created",
                "id": company.get("id", ""),
                "name": company.get("name", ""),
                "domain": company.get("domainName", ""),
            })
        except RuntimeError as e:
            err = str(e)
            if "already exists" in err.lower() or "duplicate" in err.lower():
                return json.dumps({"error": f"Company '{args['name']}' already exists. Use list_companies to find it."})
            return json.dumps({"error": err})

    # =========================================================================
    # People
    # =========================================================================

    async def _create_person(self, args: Dict[str, Any]) -> str:
        missing = [f for f in ["first_name", "last_name"] if not args.get(f)]
        if missing:
            return json.dumps({"error": f"Missing required fields: {', '.join(missing)}."})
        try:
            first = args["first_name"].replace('"', '\\"')
            last = args["last_name"].replace('"', '\\"')
            # Build data object
            parts = [f'firstName: "{first}", lastName: "{last}"']
            if args.get("job_title"):
                parts.append(f'jobTitle: "{args["job_title"].replace(chr(34), chr(92)+chr(34))}"')
            if args.get("company_id"):
                parts.append(f'companyId: "{args["company_id"]}"')

            data_fields = ", ".join(parts)
            query = f"""mutation {{
                createPerson(data: {{ name: {{ {data_fields} }} }}) {{
                    id name {{ firstName lastName }}
                }}
            }}"""
            data = self._graphql(query)
            person = data.get("createPerson", {})
            name_obj = person.get("name", {})
            return json.dumps({
                "status": "created",
                "id": person.get("id", ""),
                "name": f"{name_obj.get('firstName', '')} {name_obj.get('lastName', '')}",
            })
        except RuntimeError as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": f"Company ID '{args.get('company_id', '')}' not found. Use list_companies to find valid IDs."})
            return json.dumps({"error": err})

    # =========================================================================
    # Opportunities
    # =========================================================================

    async def _create_opportunity(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "Opportunity name is required."})
        try:
            name = args["name"].replace('"', '\\"')
            parts = [f'name: "{name}"']
            if args.get("company_id"):
                parts.append(f'companyId: "{args["company_id"]}"')
            if args.get("amount"):
                parts.append(f'amount: {args["amount"]}')

            data_fields = ", ".join(parts)
            query = f"""mutation {{
                createOpportunity(data: {{ {data_fields} }}) {{
                    id name amount
                }}
            }}"""
            data = self._graphql(query)
            opp = data.get("createOpportunity", {})
            return json.dumps({
                "status": "created",
                "id": opp.get("id", ""),
                "name": opp.get("name", ""),
                "amount": opp.get("amount"),
            })
        except RuntimeError as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": f"Company ID '{args.get('company_id', '')}' not found. Use list_companies to find valid IDs."})
            return json.dumps({"error": err})


if __name__ == "__main__":
    TwentyMCPServer().start()
