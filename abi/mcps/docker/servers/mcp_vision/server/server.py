"""Vision MCP Server — image analysis through the Opteia AI Gateway.

Routes vision (image-to-text: describe, OCR, Q&A, information extraction)
through the Opteia gateway using the ``opteia-vision`` model alias (EU
Mistral), so every call bills to the agent's tier token and honours the
Max/Enterprise-only gating enforced server-side.

No per-user credentials: the gateway key is infrastructure config read from
the ``OPTEIA_API_KEY`` environment variable (set in the agent's
``~/.hermes/.env``, the same key the ``opteia`` provider uses). An optional
per-instance override can be stored via ``login``.

Tools:
    status        — report gateway config (key presence, base URL, model)
    login         — (optional) store per-instance base_url / model / api_key
    logout        — clear stored overrides (falls back to env)
    analyze_image — describe / OCR / answer questions about an image
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Repo root (/opt/hermes-agent) so `from server.docker_base import ...` resolves
# when run directly. Provisioning also sets PYTHONPATH to the same.

from server.docker_base import ABIMCPServer
from mcp import types

# Defaults — overridable via env or per-instance login override.
DEFAULT_BASE_URL = os.environ.get("OPTEIA_BASE_URL", "https://ai.javiernevado.net").rstrip("/")
DEFAULT_MODEL = os.environ.get("OPTEIA_VISION_MODEL", "opteia-vision")
DEFAULT_PROMPT = "Describe what you see in this image in detail."
DEFAULT_MAX_TOKENS = int(os.environ.get("OPTEIA_VISION_MAX_TOKENS", "1024"))
MAX_MAX_TOKENS = 4096
REQUEST_TIMEOUT = 90

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


def _image_to_data_url(image_path: str) -> str:
    """Read a local image file and return a data:image/...;base64,... URL."""
    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")
    suffix = p.suffix.lower().lstrip(".")
    mime = _MIME.get(suffix, "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _gateway_chat(api_key: str, base_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/chat/completions to the Opteia gateway. Raises on HTTP error."""
    url = f"{base_url}/v1/chat/completions"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


class VisionMCPServer(ABIMCPServer):
    SERVICE_NAME = "vision"

    # --- config resolution: stored override > env > defaults ---
    def _resolve_config(self) -> Dict[str, Any]:
        api_key = os.environ.get("OPTEIA_API_KEY", "")
        base_url = DEFAULT_BASE_URL
        model = DEFAULT_MODEL
        overrides = self._require_creds() or {}
        if overrides.get("api_key"):
            api_key = overrides["api_key"]
        if overrides.get("base_url"):
            base_url = overrides["base_url"].rstrip("/")
        if overrides.get("model"):
            model = overrides["model"]
        return {"api_key": api_key, "base_url": base_url, "model": model}

    def _login_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "Opteia gateway API key (tier token). Optional — defaults to OPTEIA_API_KEY env.",
                },
                "base_url": {
                    "type": "string",
                    "description": f"Gateway base URL. Optional — defaults to {DEFAULT_BASE_URL}.",
                },
                "model": {
                    "type": "string",
                    "description": f"Vision model alias. Optional — defaults to {DEFAULT_MODEL}.",
                },
            },
            "required": [],
        }

    def _extra_tools(self) -> List[types.Tool]:
        return [
            types.Tool(
                name="analyze_image",
                description=(
                    "Analyze an image via the Opteia gateway vision model (EU). "
                    "Describe, OCR, answer questions about, or extract information "
                    "from an image. Bills to the configured gateway token; the "
                    "opteia-vision model is Max/Enterprise-only (gated server-side)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "image_path": {
                            "type": "string",
                            "description": "Local file path to the image (png/jpg/webp/gif/bmp).",
                        },
                        "image_url": {
                            "type": "string",
                            "description": "URL to the image (alternative to image_path).",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "What to do with the image (describe / OCR / answer a question / extract).",
                            "default": DEFAULT_PROMPT,
                        },
                        "max_tokens": {
                            "type": "integer",
                            "description": f"Max output tokens (default {DEFAULT_MAX_TOKENS}, cap {MAX_MAX_TOKENS}).",
                            "default": DEFAULT_MAX_TOKENS,
                        },
                    },
                    "required": [],
                },
            ),
        ]

    async def _handle_status(self) -> str:
        cfg = self._resolve_config()
        has_key = bool(cfg["api_key"])
        return json.dumps({
            "status": "connected" if has_key else "not_configured",
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "message": (
                "OPTEIA_API_KEY present — vision ready."
                if has_key
                else "OPTEIA_API_KEY not set in environment. Set it in ~/.hermes/.env."
            ),
        })

    async def _handle_login(self, args: Dict[str, Any]) -> str:
        # Store optional overrides only for fields explicitly provided.
        current = self._require_creds() or {}
        updates = {k: args[k] for k in ("api_key", "base_url", "model") if args.get(k)}
        if updates:
            current.update(updates)
            self._save_creds(current)
        cfg = self._resolve_config()
        return json.dumps({
            "status": "connected" if cfg["api_key"] else "not_configured",
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "message": "Vision config updated. Uses OPTEIA_API_KEY env unless overridden.",
        })

    async def _validate_credentials(self, creds: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Status/login never block on remote connectivity; analyze_image surfaces errors.
        return None

    async def _handle_service_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "analyze_image":
            return await self._analyze_image(args)
        return json.dumps({"error": f"Unknown tool: {name}"})

    async def _analyze_image(self, args: Dict[str, Any]) -> str:
        cfg = self._resolve_config()
        if not cfg["api_key"]:
            return json.dumps({
                "error": "OPTEIA_API_KEY not configured. Set it in ~/.hermes/.env or call login.",
            })

        prompt = args.get("prompt") or DEFAULT_PROMPT
        max_tokens = min(int(args.get("max_tokens") or DEFAULT_MAX_TOKENS), MAX_MAX_TOKENS)

        # Build the image content part from a local file or a URL.
        if args.get("image_path"):
            try:
                data_url = _image_to_data_url(args["image_path"])
            except FileNotFoundError as e:
                return json.dumps({"error": str(e)})
            image_part = {"type": "image_url", "image_url": {"url": data_url}}
        elif args.get("image_url"):
            image_part = {"type": "image_url", "image_url": {"url": args["image_url"]}}
        else:
            return json.dumps({"error": "Provide either image_path (local file) or image_url (URL)."})

        payload = {
            "model": cfg["model"],
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [image_part, {"type": "text", "text": prompt}],
            }],
        }

        try:
            result = _gateway_chat(cfg["api_key"], cfg["base_url"], payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            return json.dumps({"error": f"Gateway HTTP {e.code}: {body[:400]}"})
        except Exception as e:
            return json.dumps({"error": f"Gateway request failed: {e}"})

        if isinstance(result, dict) and result.get("error"):
            err = result["error"]
            return json.dumps({"error": err.get("message", str(err)) if isinstance(err, dict) else str(err)})

        choices = result.get("choices") or []
        if not choices:
            return json.dumps({"error": "No response from model", "raw": str(result)[:300]})
        text = choices[0].get("message", {}).get("content", "")
        return json.dumps({
            "status": "recognized",
            "model": cfg["model"],
            "upstream_model": result.get("model", ""),
            "description": text,
            "usage": result.get("usage", {}),
        })


if __name__ == "__main__":
    VisionMCPServer().start()
