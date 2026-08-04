"""gateway/green_light_check.py — ABI v4 green-light COUNT gate (session-start).

WHY: Phase 2 shifts license enforcement from a per-agent Worker verify to a COUNT
enforced on the box. abi-service (the on-box authority, a Go binary) holds the ONE
machine-license, verifies it (read-only), reads max_agents, and mints
≤max_agents signed short-lived GREEN-LIGHT tokens into a shared read-only
greenlights/ dir (one <container-name>.token per running agent, capped). Each
agent reads ITS OWN token at session-start and verifies the Ed25519 signature
against the published pubkey (greenlight.pub, mounted read-only). An agent with
no valid token — the (N+1)th, over-cap — FAILS CLOSED with a user-facing message.
This is the hard agent-count enforcement (memory-gating no longer deters misuse
now the tools are good). See workspace/abi-service-green-light-protocol.md.

TOKEN FORMAT (signed by abi-service in Go; language-agnostic):
    token   = base64url(payload_json) + "." + base64url(ed25519_sig)
    payload = {"agent": <name>, "fp": <fp_prefix>, "exp": <unix_sec>, "cap": <int|null>}
The body the signature covers is the raw payload_json bytes. We verify with
cryptography's Ed25519PublicKey (confirmed in /opt/data/venv) — cross-verified
live: 8/8 Go-signed tokens verify under Python; a 1-byte tamper is rejected.

IDENTITY: the token file is named after the CONTAINER (abi-agent-<name>.token),
set in compose via the ABI_AGENT_NAME env (no `hostname:` is set, so HOSTNAME is
just the short container id — unreliable). The agent reads
$ABI_GREENLIGHTS_DIR/$ABI_AGENT_NAME.token.

FAIL POLICY (mirrors gateway/license_check.py):
  - definitive failure (no token / expired / bad sig / name mismatch) → HARD
    denial: block the turn + send a user-facing message + cache for the TTL.
  - INFRA failure (greenlight.pub missing/unreadable, mount absent, or
    ABI_AGENT_NAME unset) → TRANSIENT: fail OPEN (a paying customer's bot must
    not lock out on a broken mount or a not-yet-migrated compose). An over-cap
    agent has no token regardless, so it's still caught.

TRANSITION: gated behind ABI_GREENLIGHT_GATE (default "0" = off). During the
cutover it runs ALONGSIDE the fingerprint license gate (ABI_LICENSE_GATE); once
the fleet is green-light-only, ABI_LICENSE_GATE is dropped and abi-service
becomes the sole license authority. See design §7.

PROPRIETARY (Opteia) — Forgejo private ONLY. Never publish to the public GitHub fork.
"""
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

DEFAULT_GREENLIGHTS_DIR = "/opt/abi-tools/greenlights"
PUBKEY_FILE = "greenlight.pub"

# User-facing messages — all name Opteia support; none leak the token/fp/key.
_MSG_OVER_CAP = (
    "This Opteia assistant is over its licensed agent count and can't respond right now. "
    "Please contact Opteia support to add capacity."
)
_MSG_EXPIRED = (
    "This Opteia assistant's authorization can't be renewed right now and has expired. "
    "Please contact Opteia support."
)
_MSG_BAD_TOKEN = (
    "This Opteia assistant's authorization token is invalid. Please contact Opteia support."
)

_DENIED_REASONS = ("no_token", "expired", "bad_sig", "name_mismatch")
_TRANSIENT_REASONS = ("pub_missing", "read_error", "no_agent_name")


@dataclass(frozen=True)
class GreenLightVerdict:
    """Result of a green-light verification. Never raised — every path yields one.

    ok           -> a valid, unexpired token for this agent (proceed).
    is_denied    -> HARD denial (block the turn + show user_message; cache it).
    is_transient -> infra problem (fail open; do NOT block a paying customer).
    """
    ok: bool
    reason: Optional[str]
    user_message: Optional[str]

    @property
    def is_denied(self) -> bool:
        return (not self.ok) and self.reason in _DENIED_REASONS

    @property
    def is_transient(self) -> bool:
        return (not self.ok) and self.reason in _TRANSIENT_REASONS

    @classmethod
    def ok_result(cls) -> "GreenLightVerdict":
        return cls(ok=True, reason=None, user_message=None)

    @classmethod
    def denied(cls, reason: str) -> "GreenLightVerdict":
        msg = {
            "no_token": _MSG_OVER_CAP,
            "expired": _MSG_EXPIRED,
            "bad_sig": _MSG_BAD_TOKEN,
            "name_mismatch": _MSG_BAD_TOKEN,
        }.get(reason)
        return cls(ok=False, reason=reason, user_message=msg)

    @classmethod
    def transient(cls, reason: str) -> "GreenLightVerdict":
        return cls(ok=False, reason=reason, user_message=None)


def _b64url_decode(s: str) -> bytes:
    """urlsafe base64 decode, tolerating the unpadded output Go's RawURLEncoding emits."""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_green_light(
    agent_name: str,
    greenlights_dir: str = DEFAULT_GREENLIGHTS_DIR,
    *,
    now: Optional[float] = None,
) -> GreenLightVerdict:
    """Read + verify this agent's green-light token from the shared dir.

    Never raises — every path returns a GreenLightVerdict. `now` is injectable
    for tests (epoch seconds).
    """
    if not agent_name:
        # ABI_AGENT_NAME unset — compose not yet updated. Fail OPEN so a
        # not-yet-migrated agent isn't locked out while ABI_GREENLIGHT_GATE rolls
        # out (the gate is also default-off).
        logger.warning("green-light check: ABI_AGENT_NAME unset — failing open (transient)")
        return GreenLightVerdict.transient("no_agent_name")

    now = time.time() if now is None else now

    # Load the verifying pubkey (published by abi-service into the shared dir).
    pub_path = os.path.join(greenlights_dir, PUBKEY_FILE)
    try:
        with open(pub_path, "rb") as f:
            pub = Ed25519PublicKey.from_public_bytes(f.read())
    except FileNotFoundError:
        logger.warning("green-light check: %s missing — failing open (mount/pubkey not published?)", pub_path)
        return GreenLightVerdict.transient("pub_missing")
    except Exception as e:  # malformed pubkey / wrong length — infra, fail open
        logger.warning("green-light check: cannot load pubkey %s (%s) — failing open", pub_path, e)
        return GreenLightVerdict.transient("read_error")

    # Read this agent's token.
    token_path = os.path.join(greenlights_dir, agent_name + ".token")
    try:
        with open(token_path, encoding="utf-8") as f:
            token = f.read().strip()
    except FileNotFoundError:
        # No token minted for this agent → over-cap (or abi-service hasn't run).
        # HARD denial — the count-enforcement hinge.
        return GreenLightVerdict.denied("no_token")
    except Exception as e:
        logger.warning("green-light check: cannot read %s (%s) — failing open", token_path, e)
        return GreenLightVerdict.transient("read_error")

    if "." not in token:
        return GreenLightVerdict.denied("bad_sig")
    body_b64, sig_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
        pub.verify(sig, body)  # raises InvalidSignature on mismatch / tamper
        payload = json.loads(body)
    except (InvalidSignature, ValueError, TypeError) as e:
        logger.warning("green-light check: bad token for %s (%s) — denying", agent_name, e)
        return GreenLightVerdict.denied("bad_sig")

    # The token must be FOR this agent (defends against a mis-mounted shared dir).
    if payload.get("agent") != agent_name:
        logger.warning("green-light check: token agent=%r != self=%r — denying",
                       payload.get("agent"), agent_name)
        return GreenLightVerdict.denied("name_mismatch")

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or now >= exp:
        return GreenLightVerdict.denied("expired")

    return GreenLightVerdict.ok_result()
