"""Proxmox VE MCP Server — Virtualization management accessor.

Tools:
    proxmox_status  — Check auth status
    proxmox_login   — Store API token (token_id=xxx-secret)
    proxmox_logout  — Remove credentials
    proxmox_list_vms — List VMs across cluster
    proxmox_vm_status — Get VM status
    proxmox_snapshot — Create/list VM snapshots

Auth: Proxmox API token (user@realm!tokenid=secret).
"""

import json
import sys
import ssl
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from abi.mcps.base import ABIMCPServer
from mcp import types


class ProxmoxMCPServer(ABIMCPServer):
    SERVICE_NAME = "proxmox"

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_url": {
                    "type": "string",
                    "description": "Proxmox API URL (e.g., https://pve.example.com:8006).",
                },
                "token_id": {
                    "type": "string",
                    "description": "API token ID (e.g., root@pam!mytoken).",
                },
                "token_secret": {
                    "type": "string",
                    "description": "API token secret.",
                },
                "node": {
                    "type": "string",
                    "description": "Default node name (optional).",
                },
                "verify_ssl": {
                    "type": "boolean",
                    "description": "Verify SSL certificates (default: false for self-signed).",
                    "default": False,
                },
            },
            "required": ["api_url", "token_id", "token_secret"],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            types.Tool(
                name="list_vms",
                description="List VMs across the Proxmox cluster.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": "Filter by node name (optional).",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="vm_status",
                description="Get detailed status of a specific VM.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "node": {"type": "string", "description": "Node name."},
                        "vmid": {"type": "integer", "description": "VM ID."},
                        "type": {
                            "type": "string",
                            "description": "VM type: qemu (default) or lxc.",
                            "default": "qemu",
                        },
                    },
                    "required": ["node", "vmid"],
                },
            ),
            types.Tool(
                name="snapshot",
                description="Create or list VM snapshots.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "create"],
                            "description": "Action: list or create (default: list).",
                            "default": "list",
                        },
                        "node": {"type": "string", "description": "Node name."},
                        "vmid": {"type": "integer", "description": "VM ID."},
                        "snapname": {
                            "type": "string",
                            "description": "Snapshot name (for create action).",
                        },
                        "description": {
                            "type": "string",
                            "description": "Snapshot description (optional).",
                        },
                    },
                    "required": ["node", "vmid"],
                },
            ),
        ]

    def _get_ssl_context(self, verify: bool) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            import urllib.request
            url = creds.get("api_url", "").rstrip("/")
            verify = creds.get("verify_ssl", False)
            ctx = self._get_ssl_context(verify)

            req = urllib.request.Request(
                f"{url}/api2/json/version",
                headers={
                    "Authorization": f"PVEAPIToken={creds['token_id']}={creds['token_secret']}",
                },
            )
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                return None
        except Exception as e:
            if "401" in str(e) or "403" in str(e):
                return {"valid": False, "message": "API token is invalid."}
            return None

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        api_url = args.get("api_url", "").strip()
        token_id = args.get("token_id", "").strip()
        token_secret = args.get("token_secret", "").strip()

        if not all([api_url, token_id, token_secret]):
            return json.dumps({"error": "api_url, token_id, and token_secret are required."})

        creds = {
            "api_url": api_url.rstrip("/"),
            "token_id": token_id,
            "token_secret": token_secret,
            "verify_ssl": args.get("verify_ssl", False),
        }
        if args.get("node"):
            creds["default_node"] = args["node"]

        validation = await self._validate_credentials(creds)
        if validation and not validation.get("valid"):
            return json.dumps({"error": validation.get("message", "Token validation failed.")})

        self._save_creds(creds)
        return json.dumps({"status": "connected", "message": f"Proxmox connected at {api_url}."})

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "list_vms":
            return await self._list_vms(args)
        elif name == "vm_status":
            return await self._vm_status(args)
        elif name == "snapshot":
            return await self._snapshot(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    def _api_request(self, path: str, method: str = "GET", data: Optional[Dict] = None) -> Dict:
        import urllib.request
        creds = self._require_creds()
        if not creds:
            raise ValueError("Not authenticated")

        url = f"{creds['api_url']}/api2/json/{path.lstrip('/')}"
        ctx = self._get_ssl_context(creds.get("verify_ssl", False))
        headers = {
            "Authorization": f"PVEAPIToken={creds['token_id']}={creds['token_secret']}",
            "accept": "application/json",
        }
        body = None
        if data:
            body = json.dumps(data).encode()
            headers["content-type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            if resp.status == 204 or resp.status == 200 and not resp.read():
                return {}
            resp_data = json.loads(resp.read())
            return resp_data.get("data", resp_data)

    async def _list_vms(self, args: Dict[str, Any]) -> str:
        try:
            node = args.get("node")
            if node:
                path = f"nodes/{node}/qemu"
                data = self._api_request(path)
                vms = []
                for vm in data if isinstance(data, list) else [data]:
                    vms.append({
                        "vmid": vm.get("vmid"),
                        "name": vm.get("name", ""),
                        "status": vm.get("status", ""),
                        "cpu": vm.get("cpu", 0),
                        "mem": vm.get("mem", 0),
                        "maxmem": vm.get("maxmem", 0),
                        "node": node,
                    })
            else:
                # Cluster-wide listing
                data = self._api_request("cluster/resources?type=vm")
                vms = []
                for vm in data if isinstance(data, list) else [data]:
                    vms.append({
                        "vmid": vm.get("vmid"),
                        "name": vm.get("name", ""),
                        "status": vm.get("status", ""),
                        "node": vm.get("node", ""),
                        "type": vm.get("type", ""),
                    })
            return json.dumps({"vms": vms, "count": len(vms)})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _vm_status(self, args: Dict[str, Any]) -> str:
        try:
            node = args["node"]
            vmid = args["vmid"]
            vm_type = args.get("type", "qemu")
            data = self._api_request(f"nodes/{node}/{vm_type}/{vmid}/status/current")
            return json.dumps({"status": data})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _snapshot(self, args: Dict[str, Any]) -> str:
        try:
            node = args["node"]
            vmid = args["vmid"]
            action = args.get("action", "list")

            if action == "list":
                data = self._api_request(f"nodes/{node}/qemu/{vmid}/snapshot")
                snapshots = [s for s in data if isinstance(data, list)] if isinstance(data, list) else [data]
                return json.dumps({"snapshots": snapshots, "count": len(snapshots)})
            elif action == "create":
                snapname = args.get("snapname")
                if not snapname:
                    return json.dumps({"error": "snapname is required for create action."})
                payload = {"snapname": snapname}
                if args.get("description"):
                    payload["description"] = args["description"]
                self._api_request(f"nodes/{node}/qemu/{vmid}/snapshot", method="POST", data=payload)
                return json.dumps({"status": "created", "snapshot": snapname})
            else:
                return json.dumps({"error": f"Unknown action: {action}"})
        except Exception as e:
            return json.dumps({"error": str(e)})


if __name__ == "__main__":
    ProxmoxMCPServer().start()
