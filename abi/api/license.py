"""License enforcement for ABI Memory API.

Fetches a signed JWT from api.opteia.com on startup and refreshes every 12h.
Write endpoints validate the JWT locally (HMAC-SHA256, ~0.1ms). Read endpoints
are always open. If the Worker is unreachable, a 3-day grace period allows
writes to continue.

No bypass: every instance must have OPTEIA_LICENSE_KEY set.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
import jwt

logger = logging.getLogger(__name__)

GRACE_PERIOD_SECONDS = 3 * 86400  # 3 days
JWT_REFRESH_INTERVAL = 43200      # 12 hours
VERIFY_URL = "https://api.opteia.com/license/verify"


class LicenseManager:
    """Manages JWT license validation for the API server."""

    def __init__(self) -> None:
        self._license_key: str = os.environ.get("OPTEIA_LICENSE_KEY", "")
        self._jwt_secret: str = os.environ.get("LICENSE_JWT_SECRET", "")
        self._api_version: str = os.environ.get("OPTEIA_API_VERSION", "1.0.0")
        self._verify_url: str = os.environ.get("OPTEIA_LICENSE_URL", VERIFY_URL)

        self._token: Optional[str] = None
        self._claims: Optional[Dict[str, Any]] = None
        self._fetched_at: Optional[float] = None
        self._revoked: bool = False
        self._grace_start: Optional[float] = None
        self._refresh_task: Optional[asyncio.Task] = None

        if not self._license_key:
            logger.error("OPTEIA_LICENSE_KEY not set — all writes will be blocked")

    async def fetch_jwt(self) -> bool:
        """Fetch JWT from the license Worker. Returns True on success."""
        if not self._license_key:
            self._revoked = True
            return False

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self._verify_url, params={
                    "key": self._license_key,
                    "version": self._api_version,
                })

            if resp.status_code == 200:
                data = resp.json()
                self._token = data["token"]
                self._claims = self._decode_jwt(self._token)
                self._fetched_at = time.time()
                self._revoked = False
                self._grace_start = None
                logger.info(
                    "License verified: tier=%s customer=%s expires=%s",
                    self._claims.get("tier"),
                    self._claims.get("customer"),
                    self._claims.get("expires"),
                )
                return True

            if resp.status_code == 403:
                error = resp.json().get("error", "unknown")
                logger.error("License rejected: %s", error)
                self._revoked = True
                return False

            logger.error("License verify returned %d", resp.status_code)
            self._start_grace()
            return False

        except Exception as e:
            logger.error("License verify failed (network?): %s", e)
            self._start_grace()
            return False

    def _decode_jwt(self, token: str) -> Dict[str, Any]:
        """Decode and validate JWT locally using the shared secret."""
        return jwt.decode(token, self._jwt_secret, algorithms=["HS256"])

    def is_write_allowed(self) -> bool:
        """Check if write operations are allowed."""
        if not self._license_key:
            return False

        if self._revoked:
            return False

        # Try local JWT validation
        if self._token and self._jwt_secret:
            try:
                claims = self._decode_jwt(self._token)
                if claims.get("exp", 0) > time.time():
                    return True
            except jwt.ExpiredSignatureError:
                pass
            except Exception as e:
                logger.warning("JWT validation error: %s", e)

        # Check grace period
        if self._grace_start is not None:
            elapsed = time.time() - self._grace_start
            if elapsed < GRACE_PERIOD_SECONDS:
                remaining = int((GRACE_PERIOD_SECONDS - elapsed) / 3600)
                logger.debug("Grace period active: %dh remaining", remaining)
                return True
            logger.error("Grace period expired — writes blocked")

        return False

    def _start_grace(self) -> None:
        """Start the grace period timer if not already started."""
        if self._grace_start is None:
            self._grace_start = time.time()
            logger.warning(
                "License Worker unreachable — 3-day grace period started"
            )

    def get_status(self) -> Dict[str, Any]:
        """Return current license status for health endpoint."""
        if not self._license_key:
            return {"status": "unlicensed", "tier": None, "expires": None}

        if self._revoked:
            return {"status": "revoked", "tier": None, "expires": None}

        if self._claims:
            exp = self._claims.get("exp", 0)
            if exp > time.time():
                return {
                    "status": "active",
                    "tier": self._claims.get("tier"),
                    "expires": self._claims.get("expires"),
                }

        if self._grace_start is not None:
            remaining = GRACE_PERIOD_SECONDS - (time.time() - self._grace_start)
            if remaining > 0:
                return {
                    "status": "grace",
                    "tier": self._claims.get("tier") if self._claims else None,
                    "expires": None,
                    "grace_remaining_hours": int(remaining / 3600),
                }

        return {"status": "expired", "tier": None, "expires": None}

    async def start_refresh(self) -> None:
        """Initial fetch + start background refresh loop."""
        await self.fetch_jwt()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        """Background task: refresh JWT every 12 hours."""
        while True:
            await asyncio.sleep(JWT_REFRESH_INTERVAL)
            await self.fetch_jwt()

    def stop_refresh(self) -> None:
        """Cancel background refresh task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None


# FastAPI dependency for write endpoints
from fastapi import HTTPException


def require_license():
    """FastAPI dependency: raises 403 if license is not valid for writes."""
    from .deps import get_license_manager

    mgr = get_license_manager()
    if not mgr.is_write_allowed():
        status = mgr.get_status()
        raise HTTPException(
            status_code=403,
            detail={
                "error": "license_required",
                "message": "Write access requires a valid license. Read access is always available.",
                "license_status": status["status"],
            },
        )
