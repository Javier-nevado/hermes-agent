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
from pathlib import Path
from typing import Any, Dict, Optional

import base64

import httpx
import jwt

logger = logging.getLogger(__name__)

GRACE_PERIOD_SECONDS = 3 * 86400  # 3 days
JWT_REFRESH_INTERVAL = 43200      # 12 hours (success cadence)
# On a FAILED verify, retry this often so the instance self-heals quickly
# (host clock re-syncs, transient Worker 5xx, brief network blip) instead of
# stranding the customer in the 3-day grace window for up to 12h. Field
# incident 2026-07-03: a single clock-skewed startup dropped Castor + EYETECH
# into grace until the next 12h refresh.
RETRY_INTERVAL_FAILED = int(os.environ.get("LICENSE_RETRY_INTERVAL_FAILED", "300"))  # 5 min
# JWT decode leeway (seconds) on iat/nbf/exp. Absorbs transient NTP /
# Cloudflare-edge clock skew. The previous fixed 30s was too tight — skew
# spikes during boot/NTP-correction exceeded it and rejected an otherwise
# valid token ("The token is not yet valid (iat)"). 120s default.
JWT_LEEWAY_SECONDS = int(os.environ.get("LICENSE_JWT_LEEWAY", "120"))
VERIFY_URL = "https://api.opteia.com/license/verify"


class LicenseManager:
    """Manages JWT license validation for the API server."""

    def __init__(self) -> None:
        self._license_key: str = os.environ.get("OPTEIA_LICENSE_KEY", "")
        self._jwt_secret: str = os.environ.get("LICENSE_JWT_SECRET", "")
        self._api_version: str = os.environ.get("OPTEIA_API_VERSION", "1.0.0")
        self._verify_url: str = os.environ.get("OPTEIA_LICENSE_URL", VERIFY_URL)

        self._token: Optional[str] = None  # backward-compat alias (the verified token)
        self._token_hs256: Optional[str] = None  # legacy shared-secret token
        self._token_rs256: Optional[str] = None  # public-key token (preferred)
        self._public_key_cache: Optional[str] = None  # "" caches a miss
        self._claims: Optional[Dict[str, Any]] = None
        self._fetched_at: Optional[float] = None
        self._revoked: bool = False
        self._grace_start: Optional[float] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._dek: Optional[bytes] = None
        self._last_fetch_ok: bool = False

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
                self._token_hs256 = data.get("token")
                self._token_rs256 = data.get("token_rs256")
                self._token = self._token_rs256 or self._token_hs256  # backward-compat
                self._claims = self._decode_jwt()
                self._fetched_at = time.time()
                self._revoked = False
                self._grace_start = None

                # Cache DEK for content encryption
                dek_b64 = data.get("dek")
                if dek_b64:
                    self._dek = base64.b64decode(dek_b64)
                    logger.info("DEK received from license Worker")
                else:
                    logger.warning("No DEK in license response — encryption disabled")

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
            # Transient causes (network blip, clock-skewed iat, Worker 5xx) are
            # retried shortly — see _refresh_loop. Log the real exception so a
            # clock-skew rejection isn't misread as a network outage.
            logger.error("License verify failed (will retry in %ds): %s", RETRY_INTERVAL_FAILED, e)
            self._start_grace()
            return False

    def _load_public_key(self) -> Optional[str]:
        """Lazy-load the RS256 public key shipped in the package.

        Returns the PEM string or None if absent (older builds without it). A public
        key belongs in the image, not env, to avoid a second manual-distribution
        footgun like LICENSE_JWT_SECRET. The miss is cached so we don't re-stat every
        verify.
        """
        if self._public_key_cache is not None:
            return self._public_key_cache or None
        # license.py lives at abi/api/license.py; the pubkey ships at abi/license/.
        pub_path = Path(__file__).parent.parent / "license" / "abi-license.pub"
        try:
            self._public_key_cache = pub_path.read_text()
            logger.info("Loaded RS256 license public key from %s", pub_path)
        except FileNotFoundError:
            self._public_key_cache = ""  # cache the miss (older build)
            logger.info("No RS256 public key packaged (%s) — HS256 fallback only", pub_path)
        return self._public_key_cache or None

    def _decode_jwt(self) -> Dict[str, Any]:
        """Decode + validate the license JWT, preferring RS256 (public key) over
        HS256 (shared secret).

        RS256 path: verify token_rs256 with the packaged public key — no secret on
        the host. HS256 path: legacy fallback for builds/Workers still on the shared
        secret. JWT_LEEWAY_SECONDS leeway absorbs transient NTP/Cloudflare-edge clock
        skew on iat/nbf/exp. algorithms=[] is ALWAYS explicit to defeat alg-confusion
        (alg:none / HS256-with-RSA-pubkey).
        """
        pubkey = self._load_public_key()
        if pubkey and self._token_rs256:
            claims = jwt.decode(self._token_rs256, pubkey, algorithms=["RS256"], leeway=JWT_LEEWAY_SECONDS)
            logger.info("License JWT verified via RS256 (public key)")
            return claims
        if self._token_hs256 and self._jwt_secret:
            claims = jwt.decode(self._token_hs256, self._jwt_secret, algorithms=["HS256"], leeway=JWT_LEEWAY_SECONDS)
            logger.info("License JWT verified via HS256 (legacy shared secret)")
            return claims
        raise jwt.InvalidTokenError(
            "no verifiable license token (need RS256 pubkey+token_rs256, or HS256 secret+token)"
        )

    def is_write_allowed(self) -> bool:
        """Check if write operations are allowed."""
        if not self._license_key:
            return False

        if self._revoked:
            return False

        # Try local JWT validation (RS256 preferred, HS256 fallback — see _decode_jwt)
        if self._token_rs256 or self._token_hs256:
            try:
                claims = self._decode_jwt()
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

    def get_dek(self) -> Optional[bytes]:
        """Return the cached Data Encryption Key (for content encryption)."""
        return self._dek

    # Tiers that include custom tables access
    TABLES_TIERS = {"forge", "partner", "internal"}

    def tables_enabled(self) -> bool:
        """Check if the license tier includes custom tables access.

        Tables are available on forge (paid), partner (strategic), and
        internal (dev) tiers. Trial, free, and core tiers do not include
        custom tables.
        """
        if self._claims:
            tier = self._claims.get("tier", "")
            # Also support legacy KV entries with tables_enabled flag
            if tier in self.TABLES_TIERS:
                return True
            return bool(self._claims.get("tables_enabled", False))
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
        self._last_fetch_ok = await self.fetch_jwt()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        """Background task: refresh the license JWT.

        On success, refresh every JWT_REFRESH_INTERVAL (12h). On failure,
        retry every RETRY_INTERVAL_FAILED (5 min) so the instance recovers on
        its own once the Worker is reachable again or the host clock re-syncs
        — rather than stranding the customer in the 3-day grace window for up
        to 12h (field incident: clock-skewed startup → grace until the next
        12h refresh on Castor + EYETECH, 2026-07-03).
        """
        while True:
            interval = JWT_REFRESH_INTERVAL if self._last_fetch_ok else RETRY_INTERVAL_FAILED
            await asyncio.sleep(interval)
            self._last_fetch_ok = await self.fetch_jwt()

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


def require_tables_license():
    """FastAPI dependency: raises 403 if tables not enabled in license."""
    from .deps import get_license_manager

    mgr = get_license_manager()
    if not mgr.is_write_allowed():
        raise HTTPException(
            status_code=403,
            detail={"error": "license_required", "message": "Write access requires a valid license."},
        )
    if not mgr.tables_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tables_not_enabled",
                "message": "Custom tables require Core+Forge tier or higher.",
            },
        )
