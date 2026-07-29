"""gateway/license_check.py — ABI v4 fingerprint license check (session-start).

WHY THIS EXISTS: the v4 license gate used to run at container BOOT (main-wrapper
called docker/abi-license-gate.py, which exited non-zero on a mismatch so the
gateway never started). That made licensing failures SILENT from the user's
view: a bot bound to another machine just looked dead in Telegram — no message
told anyone to contact support. The gate now runs at SESSION START instead: the
gateway always boots (Telegram connects), and on the first user message it
verifies the license; on a hard denial it replies with a user-facing "contact
Opteia support" message and ends the turn (no LLM call). The caller (run.py)
TTL-caches the verdict. See memory/abi-v4-fingerprint-licensing.

THE MODEL (unchanged): a license binds to ONE machine fingerprint =
sha256(product_uuid). The fingerprint is computed by ROOT at boot (product_uuid
is mode 0400) and handed to the non-root gateway via the ABI_FINGERPRINT env var
(main-wrapper exports it before exec'ing hermes). verify_license is the
read-only verifier the gateway calls each session.

WORKER CONTRACT (Phase B, deployed api.opteia.com v754c6ae4):
  POST {ABI_SEAT_API_URL}/license/verify   Headers: X-License-Key, User-Agent
       Body: {"fingerprint": <fp>}
       200 {"tier","customer"}                 -> match -> OK
       403 {"error":"fingerprint_mismatch"}    -> bound to a different machine
       403 {"error":"not_activated"}           -> no binding yet -> lazy activate
       401/403 {"error":"invalid_key"}          -> invalid
  POST /license/activate   (lazy first-bind, ONLY when verify says not_activated)
       200 {"tier","bound"}                    -> this fp bound (just-now/idempotent) -> OK
       409 {"error":"bound_to_different_machine"} -> bound elsewhere
       401/403 {"error":"invalid_key"|"license_revoked"} -> invalid

verify is STRICTLY read-only (never writes per session). The only write is the
single first-bind activate, which is idempotent (write-free after the first bind)
— that is what keeps the hot path under the KV free-tier quota (no CF 1101).

FAIL POLICY (fail-open, confirmed): a definitive denial (bound_elsewhere /
invalid_key) returns is_denied so the caller blocks + shows the user-facing
message (and caches it for the TTL). A TRANSIENT failure (network error, CF edge
block from a bad User-Agent, 404 route drift, 5xx) returns is_transient so the
caller FAILS OPEN (proceeds normally, does NOT cache a denial) — a paying
customer's bot must not lock out on an api.outage; a clone has a different
fingerprint and is caught the moment the API is back. An unparseable WRITE result
(activate returns something unclassifiable) fails CLOSED as not_activated, since
the binding state is genuinely uncertain.

PROPRIETARY (Opteia) — Forgejo private ONLY. Never publish to the public GitHub fork.
"""
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# api.opteia.com sits behind Cloudflare Bot Fight Mode, which 403s httpx's default
# python-marked User-Agent (edge "error code: 1010"). Every api.opteia.com client
# must send a non-default UA. Matches the boot gate's UA; env-overridable by caller.
DEFAULT_USER_AGENT = "abi-license-gate/1.0 (+https://opteia.com)"

# --- user-facing messages (all name Opteia support; none leak fp/key) ----------
_MSG_BOUND_ELSEWHERE = (
    "This Opteia assistant is licensed to a different machine and can't respond here. "
    "Please contact Opteia support to rebind the license."
)
_MSG_INVALID_KEY = (
    "This Opteia assistant's license key is invalid. Please contact Opteia support."
)
_MSG_NOT_ACTIVATED = (
    "This Opteia assistant hasn't been activated yet. Please contact Opteia support."
)

# Reasons that block the turn + show a user-facing message (cached for the TTL).
_DENIED_REASONS = ("bound_elsewhere", "invalid_key", "not_activated")
# Reasons that fail open (transient) — never cached as a denial.
_TRANSIENT_REASONS = ("transient_error", "edge_block")


@dataclass(frozen=True)
class LicenseVerdict:
    """Result of a license verification. Never raised — every path yields one.

    ok           -> license valid for this machine (proceed).
    is_denied    -> HARD denial (block the turn + show user_message; cache it).
    is_transient -> indeterminate failure (fail open; do NOT cache a denial).
    """
    ok: bool
    reason: Optional[str]
    user_message: Optional[str]
    tier: Optional[str] = None

    @property
    def is_denied(self) -> bool:
        return (not self.ok) and self.reason in _DENIED_REASONS

    @property
    def is_transient(self) -> bool:
        return (not self.ok) and self.reason in _TRANSIENT_REASONS

    @classmethod
    def ok_result(cls, tier: Optional[str]) -> "LicenseVerdict":
        return cls(ok=True, reason=None, user_message=None, tier=tier)

    @classmethod
    def denied(cls, reason: str) -> "LicenseVerdict":
        msg = {
            "bound_elsewhere": _MSG_BOUND_ELSEWHERE,
            "invalid_key": _MSG_INVALID_KEY,
            "not_activated": _MSG_NOT_ACTIVATED,
        }.get(reason)
        return cls(ok=False, reason=reason, user_message=msg)

    @classmethod
    def transient(cls, reason: str) -> "LicenseVerdict":
        return cls(ok=False, reason=reason, user_message=None)


def _edge_error(body: str) -> Optional[str]:
    """A Worker 401/403/409 body is JSON {"error": ...}. A NON-JSON 403 is a
    Cloudflare edge block (Bot Fight Mode 'error code: 1010' from a banned UA) —
    a transport problem, not a license problem. Returns the error string or None."""
    body = (body or "").strip()
    if not body.startswith("{"):
        return None
    try:
        return json.loads(body).get("error")
    except (ValueError, TypeError):
        return None


async def _post(client: httpx.AsyncClient, url: str, fp: str, key: str,
                ua: str, timeout: float):
    """POST {"fingerprint": fp} with license headers. Returns (status, body_text).
    status == 0 signals a transport-level failure (network/DNS/timeout/refused)."""
    try:
        resp = await client.post(
            url,
            json={"fingerprint": fp},
            headers={
                "User-Agent": ua,
                "X-License-Key": key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        return resp.status_code, resp.text
    except Exception as e:  # network / DNS / timeout / connection refused — fail open
        return 0, str(e)


def _is_transient_status(status: int) -> bool:
    """Server-side / route-level failures we treat as transient (fail open)."""
    return status == 0 or status == 404 or status >= 500


def _interpret_verify(status: int, body: str) -> Optional[LicenseVerdict]:
    """Classify a /license/verify response. Returns None ONLY for not_activated
    (the signal to perform the lazy first-bind activate); a verdict otherwise."""
    if status == 200:
        tier = None
        try:
            tier = json.loads(body).get("tier")
        except (ValueError, TypeError):
            pass
        return LicenseVerdict.ok_result(tier)
    err = _edge_error(body)
    if _is_transient_status(status):
        logger.warning("license verify transient (HTTP %s): %s", status, (body or "")[:160])
        return LicenseVerdict.transient("transient_error")
    if err == "fingerprint_mismatch":
        return LicenseVerdict.denied("bound_elsewhere")
    if err == "not_activated":
        return None  # -> caller does the lazy activate
    if err in ("invalid_key", "license_revoked") or status == 401:
        return LicenseVerdict.denied("invalid_key")
    if status == 403 and err is None:  # non-JSON 403 = CF Bot Fight Mode (bad UA)
        logger.warning("license verify non-JSON 403 — likely CF Bot Fight Mode "
                       "(bad User-Agent): %r", (body or "")[:120])
        return LicenseVerdict.transient("edge_block")
    # JSON 403 with an unrecognized error code — treat as invalid (definitive denial).
    if status in (403, 409):
        return LicenseVerdict.denied("invalid_key")
    logger.warning("license verify unexpected HTTP %s: %s", status, (body or "")[:160])
    return LicenseVerdict.transient("transient_error")


def _interpret_activate(status: int, body: str) -> LicenseVerdict:
    """Classify a /license/activate response (the lazy first-bind)."""
    if status == 200:
        tier = None
        try:
            tier = json.loads(body).get("tier")
        except (ValueError, TypeError):
            pass
        return LicenseVerdict.ok_result(tier)  # bound to this fp (just now or already)
    err = _edge_error(body)
    if _is_transient_status(status):
        logger.warning("license activate transient (HTTP %s): %s", status, (body or "")[:160])
        return LicenseVerdict.transient("transient_error")
    if err == "bound_to_different_machine" or status == 409:
        return LicenseVerdict.denied("bound_elsewhere")
    if err in ("invalid_key", "license_revoked") or status == 401:
        return LicenseVerdict.denied("invalid_key")
    if status == 403 and err is None:  # non-JSON 403 = CF Bot Fight Mode (bad UA)
        logger.warning("license activate non-JSON 403 — likely CF Bot Fight Mode: %r",
                       (body or "")[:120])
        return LicenseVerdict.transient("edge_block")
    if status in (403,):
        return LicenseVerdict.denied("invalid_key")
    # Unparseable WRITE result — the binding state is genuinely uncertain, so be
    # conservative: surface "not activated, contact support" rather than grant access.
    logger.warning("license activate unclassifiable HTTP %s: %s", status, (body or "")[:160])
    return LicenseVerdict.denied("not_activated")


async def verify_license(
    fingerprint: str,
    license_key: str,
    api_base: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 15.0,
) -> LicenseVerdict:
    """Verify the machine fingerprint against the license Worker (read-only).

    Lazy-binds on first use: if verify reports not_activated, performs a single
    activate (the one idempotent first-bind write) and classifies its result.
    Never raises — every path returns a LicenseVerdict.
    """
    api_base = (api_base or "").rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        v_status, v_body = await _post(
            client, f"{api_base}/license/verify", fingerprint, license_key, user_agent, timeout
        )
        verdict = _interpret_verify(v_status, v_body)
        if verdict is not None:
            return verdict
        # not_activated -> lazy first-bind via activate, then classify.
        a_status, a_body = await _post(
            client, f"{api_base}/license/activate", fingerprint, license_key, user_agent, timeout
        )
        return _interpret_activate(a_status, a_body)
