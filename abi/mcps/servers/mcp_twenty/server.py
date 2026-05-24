"""Twenty CRM MCP Server — CRM API accessor.

Tools:
    twenty_status       — Check auth status
    twenty_login        — Store API key
    twenty_logout       — Remove credentials
    twenty_list_companies — List companies in CRM
    twenty_create_company — Create a new company
    twenty_create_person  — Create a person/contact
    twenty_create_opportunity — Create an opportunity

Auth: API key from Twenty CRM settings.
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
                description="List companies from the CRM. Supports pagination with cursor.",
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
                            "description": "Pagination cursor from previous response (pageInfo.endCursor).",
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
                        "industry": {"type": "string", "description": "Industry."},
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
                        "company_id": {"type": "string", "description": "Company ID."},
                        "amount": {"type": "number", "description": "Deal amount."},
                        "stage": {"type": "string", "description": "Pipeline stage."},
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        import urllib.request
        import urllib.error
        try:
            base_url = creds.get("base_url", "").rstrip("/")
            req = urllib.request.Request(
                f"{base_url}/rest/companies?limit=1",
                headers={
                    "Authorization": f"Bearer {creds.get('api_key', '')}",
                    "accept": "application/json",
                    "User-Agent": "Opteia-ABI-MCP/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return None
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 401:
                return {"valid": False, "message": "API key is invalid or revoked. Get a new key from Twenty CRM Settings > API."}
            if e.code == 403:
                return {"valid": False, "message": "API key does not have permission to access companies. Check key permissions."}
            return {"valid": False, "message": f"HTTP {e.code}: {body}"}
        except Exception as e:
            return None

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        api_key = args.get("api_key", "").strip()
        base_url = args.get("base_url", "").strip()
        if not api_key or not base_url:
            return json.dumps({"error": "api_key and base_url are required."})

        self._save_creds({"api_key": api_key, "base_url": base_url.rstrip("/")})
        return json.dumps({"status": "connected", "message": "Twenty CRM connected."})

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "list_companies":
            return await self._list_companies(args)
        elif name == "create_company":
            return await self._create_company(args)
        elif name == "create_person":
            return await self._create_person(args)
        elif name == "create_opportunity":
            return await self._create_opportunity(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    def _api_request(self, method: str, path: str, data: Optional[Dict] = None) -> Dict:
        """Make an authenticated REST request to Twenty CRM."""
        import urllib.request
        import urllib.error
        creds = self._require_creds()
        if not creds:
            raise ValueError("Not authenticated. Call twenty_login first.")

        base_url = creds["base_url"].rstrip("/")
        url = f"{base_url}/rest/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {creds['api_key']}",
            "accept": "application/json",
            "content-type": "application/json",
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

    def _graphql_request(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Make a GraphQL request to Twenty CRM."""
        import urllib.request
        import urllib.error
        creds = self._require_creds()
        if not creds:
            raise ValueError("Not authenticated. Call twenty_login first.")

        base_url = creds["base_url"].rstrip("/")
        url = f"{base_url}/graphql"
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        data = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {creds['api_key']}",
            "content-type": "application/json",
            "User-Agent": "Opteia-ABI-MCP/1.0",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            if result.get("errors"):
                msgs = [e.get("message", str(e)) for e in result["errors"]]
                raise RuntimeError(f"GraphQL errors: {'; '.join(msgs)}")
            return result.get("data", {})
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code} on GraphQL: {error_body}") from e

    async def _list_companies(self, args: Dict[str, Any]) -> str:
        try:
            limit = min(args.get("limit", 20), 100)
            cursor = args.get("cursor", "").strip()
            # Build GraphQL query with cursor-based pagination
            after_clause = f', after: "{cursor}"' if cursor else ""
            query = f"""{{ companies(first: {limit}{after_clause}) {{
                edges {{ node {{ id name domainName }} }}
                pageInfo {{ hasNextPage endCursor }}
                totalCount
            }} }}"""
            data = self._graphql_request(query)
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
            return json.dumps({"error": "Company name is required. Provide the 'name' parameter."})
        try:
            payload = {"name": args["name"]}
            if args.get("domain"):
                payload["domainName"] = args["domain"]
            if args.get("industry"):
                payload["industry"] = args["industry"]
            data = self._api_request("POST", "companies", payload)
            return json.dumps({"status": "created", "company": data.get("data", data)})
        except RuntimeError as e:
            err = str(e)
            if "already exists" in err.lower() or "duplicate" in err.lower():
                return json.dumps({"error": f"Company '{args['name']}' already exists. Use list_companies to find existing companies."})
            return json.dumps({"error": err})
        except Exception as e:
            return json.dumps({"error": f"Failed to create company: {e}"})

    async def _create_person(self, args: Dict[str, Any]) -> str:
        missing = [f for f in ["first_name", "last_name"] if not args.get(f)]
        if missing:
            return json.dumps({"error": f"Missing required fields: {', '.join(missing)}. Provide first_name and last_name."})
        try:
            payload = {
                "firstName": args["first_name"],
                "lastName": args["last_name"],
            }
            if args.get("email"):
                payload["email"] = args["email"]
            data = self._api_request("POST", "people", payload)
            person_id = data.get("data", data).get("id", "")
            # Link to company if specified
            if args.get("company_id") and person_id:
                try:
                    self._api_request("POST", "personCompanies", {
                        "personId": person_id,
                        "companyId": args["company_id"],
                    })
                except RuntimeError as link_err:
                    return json.dumps({"status": "created", "person": data.get("data", data), "warning": f"Person created but company link failed: {link_err}"})
            return json.dumps({"status": "created", "person": data.get("data", data)})
        except RuntimeError as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": f"Company ID '{args.get('company_id', '')}' not found. Use list_companies to find valid IDs."})
            return json.dumps({"error": err})
        except Exception as e:
            return json.dumps({"error": f"Failed to create person: {e}"})

    async def _create_opportunity(self, args: Dict[str, Any]) -> str:
        if not args.get("name"):
            return json.dumps({"error": "Opportunity name is required. Provide the 'name' parameter."})
        try:
            payload = {"name": args["name"]}
            if args.get("company_id"):
                payload["companyId"] = args["company_id"]
            if args.get("amount"):
                payload["amount"] = args["amount"]
            if args.get("stage"):
                payload["stage"] = args["stage"]
            data = self._api_request("POST", "opportunities", payload)
            return json.dumps({"status": "created", "opportunity": data.get("data", data)})
        except RuntimeError as e:
            err = str(e)
            if "404" in err:
                return json.dumps({"error": f"Company ID '{args.get('company_id', '')}' not found. Use list_companies to find valid IDs."})
            return json.dumps({"error": err})
        except Exception as e:
            return json.dumps({"error": f"Failed to create opportunity: {e}"})


if __name__ == "__main__":
    TwentyMCPServer().start()
