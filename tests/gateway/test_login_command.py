"""Tests for the /login gateway slash command (in-chat OAuth device-code flow).

Covers GatewayRunner._handle_login_command (arg parsing, provider resolution,
concurrent-login guard) and the _run_codex_login_poller background task
(poll/exchange/persist + chat notification on success and timeout).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/login", platform=Platform.TELEGRAM,
                user_id="12345", chat_id="67890"):
    """Build a MessageEvent for testing."""
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    """Create a bare GatewayRunner with the minimal attrs the login path needs."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._background_tasks = set()
    runner._pending_logins = {}
    runner.session_store = None
    runner.config = None
    return runner


def _capture_create_task():
    """Patch asyncio.create_task so spawned coroutines are closed (not run)."""
    def _cap(coro, *args, **kwargs):
        coro.close()
        return MagicMock()
    return patch("gateway.run.asyncio.create_task", side_effect=_cap)


# ---------------------------------------------------------------------------
# _handle_login_command — arg parsing & provider resolution
# ---------------------------------------------------------------------------


class TestHandleLoginCommand:
    """Tests for GatewayRunner._handle_login_command."""

    @pytest.mark.asyncio
    async def test_no_args_shows_help(self):
        runner = _make_runner()
        result = await runner._handle_login_command(_make_event(text="/login"))
        assert "openai-codex" in result
        assert "/login" in result

    @pytest.mark.asyncio
    async def test_unknown_provider_shows_help(self):
        runner = _make_runner()
        result = await runner._handle_login_command(_make_event(text="/login foobar"))
        assert "Unknown provider" in result
        assert "openai-codex" in result

    @pytest.mark.asyncio
    async def test_unimplemented_provider_hints_cli(self):
        """A known provider without an in-chat flow points to the CLI."""
        runner = _make_runner()
        result = await runner._handle_login_command(_make_event(text="/login nous"))
        assert "isn't available in-chat" in result
        assert "hermes auth add" in result

    @pytest.mark.asyncio
    async def test_codex_alias_starts_flow(self):
        runner = _make_runner()
        with patch("hermes_cli.auth.request_codex_device_code") as req, _capture_create_task():
            req.return_value = {
                "user_code": "ABCD-WXYZ", "device_auth_id": "daid",
                "interval": 5, "verification_url": "https://auth.openai.com/codex/device",
                "expires_in": 900, "issuer": "https://auth.openai.com",
                "client_id": "cid",
            }
            result = await runner._handle_login_command(_make_event(text="/login codex"))
        req.assert_called_once()
        assert "https://auth.openai.com/codex/device" in result
        assert "`ABCD-WXYZ`" in result  # backticked so Telegram renders the hyphen
        assert "15 minutes" in result

    @pytest.mark.asyncio
    async def test_openai_alias_starts_flow(self):
        runner = _make_runner()
        with patch("hermes_cli.auth.request_codex_device_code") as req, _capture_create_task():
            req.return_value = {
                "user_code": "UC-1", "device_auth_id": "daid", "interval": 3,
                "verification_url": "https://auth.openai.com/codex/device",
                "expires_in": 900, "issuer": "i", "client_id": "c",
            }
            result = await runner._handle_login_command(_make_event(text="/login openai"))
        req.assert_called_once()
        assert "UC-1" in result

    @pytest.mark.asyncio
    async def test_canonical_provider_arg_starts_flow(self):
        runner = _make_runner()
        with patch("hermes_cli.auth.request_codex_device_code") as req, _capture_create_task():
            req.return_value = {
                "user_code": "UC-2", "device_auth_id": "daid", "interval": 3,
                "verification_url": "u", "expires_in": 900, "issuer": "i", "client_id": "c",
            }
            await runner._handle_login_command(_make_event(text="/login openai-codex"))
        req.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_failure_returns_error(self):
        runner = _make_runner()
        with patch("hermes_cli.auth.request_codex_device_code",
                   side_effect=RuntimeError("boom")), _capture_create_task():
            result = await runner._handle_login_command(_make_event(text="/login codex"))
        assert "Couldn't start login" in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_concurrent_login_rejected(self):
        """A second /login while one is pending is rejected."""
        runner = _make_runner()
        event = _make_event(text="/login codex")
        session_key = runner._session_key_for_source(event.source)
        runner._pending_logins[session_key] = {"provider": "openai-codex"}
        result = await runner._handle_login_command(event)
        assert "already in progress" in result

    @pytest.mark.asyncio
    async def test_start_registers_pending_login(self):
        runner = _make_runner()
        with patch("hermes_cli.auth.request_codex_device_code") as req, _capture_create_task():
            req.return_value = {
                "user_code": "UC-3", "device_auth_id": "daid-9", "interval": 3,
                "verification_url": "u", "expires_in": 900, "issuer": "i", "client_id": "c",
            }
            event = _make_event(text="/login codex")
            await runner._handle_login_command(event)
        session_key = runner._session_key_for_source(event.source)
        assert session_key in runner._pending_logins
        assert runner._pending_logins[session_key]["device_auth_id"] == "daid-9"


# ---------------------------------------------------------------------------
# _run_codex_login_poller — background poll/exchange/persist + notification
# ---------------------------------------------------------------------------


def _dc(**overrides):
    base = {
        "user_code": "UC", "device_auth_id": "DA", "interval": 0,
        "verification_url": "u", "expires_in": 60, "issuer": "i", "client_id": "c",
    }
    base.update(overrides)
    return base


class TestRunCodexLoginPoller:
    """Tests for GatewayRunner._run_codex_login_poller."""

    @pytest.mark.asyncio
    async def test_success_notifies_and_persists(self):
        runner = _make_runner()
        adapter = AsyncMock()
        runner.adapters = {Platform.TELEGRAM: adapter}

        source = _make_event(text="/login codex").source
        session_key = "sk-1"
        runner._pending_logins[session_key] = {"provider": "openai-codex"}

        with patch("hermes_cli.auth.poll_codex_device_code",
                   return_value={"authorization_code": "ac", "code_verifier": "cv"}), \
             patch("hermes_cli.auth.exchange_codex_device_code",
                   return_value={"tokens": {"access_token": "at", "refresh_token": "rt"},
                                 "base_url": "b", "last_refresh": "lr"}), \
             patch("hermes_cli.auth._save_codex_tokens") as save:
            await runner._run_codex_login_poller(source, session_key, _dc())

        save.assert_called_once_with({"access_token": "at", "refresh_token": "rt"}, "lr")
        # A confirmation message was sent to the chat.
        assert adapter.send.await_count >= 1
        sent = adapter.send.await_args
        assert "Codex connected" in sent.args[1]
        # Pending login cleared on completion.
        assert session_key not in runner._pending_logins

    @pytest.mark.asyncio
    async def test_timeout_notifies_and_clears_pending(self):
        runner = _make_runner()
        adapter = AsyncMock()
        runner.adapters = {Platform.TELEGRAM: adapter}

        source = _make_event(text="/login codex").source
        session_key = "sk-2"
        runner._pending_logins[session_key] = {"provider": "openai-codex"}

        # expires_in=0 -> the poll loop never executes -> code_resp None -> timeout
        with patch("hermes_cli.auth.poll_codex_device_code", return_value=None), \
             patch("hermes_cli.auth.exchange_codex_device_code") as exchange, \
             patch("hermes_cli.auth._save_codex_tokens") as save:
            await runner._run_codex_login_poller(source, session_key, _dc(expires_in=0))

        exchange.assert_not_called()
        save.assert_not_called()
        assert adapter.send.await_count >= 1
        assert "timed out" in adapter.send.await_args.args[1]
        assert session_key not in runner._pending_logins

    @pytest.mark.asyncio
    async def test_autherror_during_poll_notifies(self):
        runner = _make_runner()
        adapter = AsyncMock()
        runner.adapters = {Platform.TELEGRAM: adapter}
        source = _make_event(text="/login codex").source

        from hermes_cli.auth import AuthError
        with patch("hermes_cli.auth.poll_codex_device_code",
                   side_effect=AuthError("poll broke", provider="openai-codex",
                                         code="device_code_poll_error")), \
             patch("hermes_cli.auth.exchange_codex_device_code") as exchange, \
             patch("hermes_cli.auth._save_codex_tokens"):
            await runner._run_codex_login_poller(source, "sk-3", _dc())

        exchange.assert_not_called()
        assert adapter.send.await_count >= 1
        assert "Codex login failed" in adapter.send.await_args.args[1]
