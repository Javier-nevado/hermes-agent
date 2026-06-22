"""Tests for the always-admin floor on sensitive slash commands (/login).

/login writes OAuth credentials, so it must stay admin-only even when
slash-access gating is disabled (the default, where every allowed user is
treated as admin). Covers GatewayRunner._check_slash_access's
_ALWAYS_ADMIN_COMMANDS floor and the _is_home_channel_owner fallback.
"""

from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.session import SessionSource


def _make_source(*, platform=Platform.TELEGRAM, user_id="u1", chat_id="c1",
                 chat_type="dm", thread_id=None) -> SessionSource:
    return SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name=f"name-{user_id}",
        chat_type=chat_type,
        thread_id=thread_id,
    )


def _make_runner(*, extra=None, home=None, platform=Platform.TELEGRAM):
    """Bare GatewayRunner with a real config so policy_for_source + the
    home-channel check both work end-to-end."""
    from gateway.run import GatewayRunner

    pc = PlatformConfig(enabled=True, token="***", extra=extra or {})
    if home is not None:
        pc.home_channel = home
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={platform: pc})
    return runner


# ---------------------------------------------------------------------------
# _is_home_channel_owner
# ---------------------------------------------------------------------------


class TestIsHomeChannelOwner:
    def test_matches_home_channel_chat(self):
        runner = _make_runner(home=HomeChannel(Platform.TELEGRAM, "c1", "op"))
        assert runner._is_home_channel_owner(_make_source(chat_id="c1")) is True

    def test_rejects_other_chat(self):
        runner = _make_runner(home=HomeChannel(Platform.TELEGRAM, "c1", "op"))
        assert runner._is_home_channel_owner(_make_source(chat_id="c2")) is False

    def test_matches_home_channel_thread(self):
        runner = _make_runner(home=HomeChannel(Platform.TELEGRAM, "c1", "op", thread_id="99"))
        assert runner._is_home_channel_owner(
            _make_source(chat_id="c1", thread_id="99")) is True

    def test_rejects_wrong_thread(self):
        runner = _make_runner(home=HomeChannel(Platform.TELEGRAM, "c1", "op", thread_id="99"))
        assert runner._is_home_channel_owner(
            _make_source(chat_id="c1", thread_id="77")) is False

    def test_no_home_channel_trusts_dm_only(self):
        runner = _make_runner(home=None)
        assert runner._is_home_channel_owner(_make_source(chat_type="dm")) is True
        assert runner._is_home_channel_owner(_make_source(chat_type="group")) is False


# ---------------------------------------------------------------------------
# _check_slash_access — /login always-admin floor
# ---------------------------------------------------------------------------


class TestLoginAdminFloor:
    def test_allowed_for_home_channel_owner_gating_disabled(self):
        runner = _make_runner(home=HomeChannel(Platform.TELEGRAM, "c1", "op"))
        assert runner._check_slash_access(_make_source(chat_id="c1"), "login") is None

    def test_denied_for_non_owner_gating_disabled(self):
        runner = _make_runner(home=HomeChannel(Platform.TELEGRAM, "c1", "op"))
        result = runner._check_slash_access(
            _make_source(chat_id="c-other", chat_type="group"), "login")
        assert result is not None
        assert "admin-only" in result

    def test_allowed_in_dm_when_no_home_channel(self):
        runner = _make_runner(home=None)
        assert runner._check_slash_access(_make_source(chat_type="dm"), "login") is None

    def test_denied_in_group_when_no_home_channel(self):
        runner = _make_runner(home=None)
        result = runner._check_slash_access(_make_source(chat_type="group"), "login")
        assert result is not None
        assert "admin-only" in result

    def test_allowed_for_admin_when_gating_enabled(self):
        runner = _make_runner(extra={"allow_admin_from": ["admin1"]})
        assert runner._check_slash_access(
            _make_source(user_id="admin1"), "login") is None

    def test_denied_for_non_admin_when_gating_enabled(self):
        runner = _make_runner(extra={"allow_admin_from": ["admin1"]})
        result = runner._check_slash_access(_make_source(user_id="other"), "login")
        assert result is not None
        assert "admin-only" in result

    def test_non_sensitive_command_unaffected_when_gating_disabled(self):
        """Pre-existing behavior preserved: /model stays allowed for everyone
        when gating is disabled (only _ALWAYS_ADMIN_COMMANDS get the floor)."""
        runner = _make_runner(home=None)
        assert runner._check_slash_access(
            _make_source(chat_id="c1", chat_type="group"), "model") is None
