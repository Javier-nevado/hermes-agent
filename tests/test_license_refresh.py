"""Regression tests for abi/api/license.py.

Covers the 2026-07-03 field incident where a clock-skewed startup dropped
Castor + EYETECH into grace mode until the next 12h refresh:

1. ``_decode_jwt`` must tolerate iat clock skew within ``JWT_LEEWAY_SECONDS``
   (the previous fixed 30s leeway rejected valid tokens during boot/NTP skew).
2. ``_refresh_loop`` must retry quickly (``RETRY_INTERVAL_FAILED``) after a
   failed verify and only resume the long ``JWT_REFRESH_INTERVAL`` cadence
   once a fetch succeeds — so a transient failure self-heals in minutes,
   not up to 12h.
"""

from __future__ import annotations

import asyncio
import time

import jwt as pyjwt
import pytest

from abi.api import license as L


def _make_manager(**attrs):
    """Build a LicenseManager without running __init__ (no env needed)."""
    mgr = L.LicenseManager.__new__(L.LicenseManager)
    # Sensible defaults; caller overrides via attrs.
    mgr._public_key_cache = ""  # "" -> _load_public_key returns None (HS256 path)
    mgr._token_rs256 = None
    mgr._token_hs256 = None
    mgr._jwt_secret = ""
    mgr._last_fetch_ok = False
    mgr._refresh_task = None
    for k, v in attrs.items():
        setattr(mgr, k, v)
    return mgr


# --------------------------------------------------------------------------- #
# 1. Leeway absorbs clock-skewed iat
# --------------------------------------------------------------------------- #

def test_decode_jwt_tolerates_iat_skew_within_leeway():
    """A token issued slightly in the future (clock skew) must verify.

    With the old fixed 30s leeway, iat=now+60 raised ImmatureSignatureError
    ("The token is not yet valid (iat)"). With the configurable 120s default,
    a 60s skew verifies cleanly.
    """
    secret = "test-shared-secret-32bytes-min!!"
    now = int(time.time())
    skew = 60  # > old 30s leeway, < new 120s default
    token = pyjwt.encode(
        {"tier": "trial", "customer": "Castor", "iat": now + skew, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )
    mgr = _make_manager(_jwt_secret=secret, _token_hs256=token)

    claims = mgr._decode_jwt()  # must NOT raise
    assert claims["customer"] == "Castor"


def test_decode_jwt_still_rejects_skew_beyond_leeway():
    """Leeway is not infinite — a large future-dated iat is still rejected."""
    secret = "test-shared-secret-32bytes-min!!"
    now = int(time.time())
    skew = L.JWT_LEEWAY_SECONDS + 60  # beyond tolerance
    token = pyjwt.encode(
        {"tier": "trial", "customer": "X", "iat": now + skew, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )
    mgr = _make_manager(_jwt_secret=secret, _token_hs256=token)

    with pytest.raises(Exception):
        mgr._decode_jwt()


# --------------------------------------------------------------------------- #
# 2. _refresh_loop retries fast on failure, slow on success
# --------------------------------------------------------------------------- #

class _StopLoop(Exception):
    """Sentinel raised from the patched sleep to break the infinite loop."""


def test_refresh_loop_retries_fast_then_resumes_long_interval(monkeypatch):
    """On failure the loop sleeps RETRY_INTERVAL_FAILED; on success it
    resumes JWT_REFRESH_INTERVAL. This is what lets a clock-skewed or
    network-blipped instance self-heal in minutes instead of 12h."""
    mgr = _make_manager(_last_fetch_ok=False)
    # fetch fails once, then succeeds.
    fetch_results = iter([False, True, True])

    async def fake_fetch(self):  # noqa: ANN001
        return next(fetch_results)

    monkeypatch.setattr(L.LicenseManager, "fetch_jwt", fake_fetch)

    sleeps: list[int] = []

    async def fake_sleep(interval):
        sleeps.append(interval)
        if len(sleeps) >= 3:
            raise _StopLoop()

    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        asyncio.run(mgr._refresh_loop())

    # iter1: failed  -> sleep RETRY (300)
    # iter2: failed  -> sleep RETRY (300)
    # iter3: success -> sleep REFRESH (43200)  [then _StopLoop breaks]
    assert sleeps == [L.RETRY_INTERVAL_FAILED, L.RETRY_INTERVAL_FAILED, L.JWT_REFRESH_INTERVAL]


def test_refresh_loop_uses_short_interval_when_last_fetch_failed(monkeypatch):
    """Explicit: a failed last fetch selects the short retry interval."""
    mgr = _make_manager(_last_fetch_ok=False)
    chosen: list[int] = []

    async def fake_sleep(interval):
        chosen.append(interval)
        raise _StopLoop()

    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        asyncio.run(mgr._refresh_loop())

    assert chosen == [L.RETRY_INTERVAL_FAILED]


# --------------------------------------------------------------------------- #
# 3. Defaults are sane
# --------------------------------------------------------------------------- #

def test_defaults():
    assert L.JWT_LEEWAY_SECONDS >= 60, "leeway must comfortably exceed typical NTP skew"
    assert 60 <= L.RETRY_INTERVAL_FAILED <= 3600, "failed-retry should be 1–60 min"
    assert L.RETRY_INTERVAL_FAILED < L.JWT_REFRESH_INTERVAL
