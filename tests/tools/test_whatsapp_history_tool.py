"""Tests for the WhatsApp history-reading tools in tools/send_message_tool.py.

These tools (read_whatsapp_history, list_whatsapp_chats) call the local
WhatsApp bridge's GET /history and GET /chats endpoints. We mock the bridge
HTTP layer (aiohttp) and the gateway-config lookup so no network or live
bridge is required — mirroring TestSendToPlatformWhatsapp's approach.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.send_message_tool import (
    _list_whatsapp_chats,
    _read_whatsapp_history,
    list_whatsapp_chats_tool,
    read_whatsapp_history_tool,
)


# --- Minimal async-context-manager fakes for aiohttp.ClientSession ---------

class _AsyncCtx:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


class _FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    """A stand-in for aiohttp.ClientSession returning a preset response."""

    def __init__(self, resp):
        self._resp = resp

    def get(self, *args, **kwargs):
        return _AsyncCtx(self._resp)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_session(resp):
    """Patch aiohttp.ClientSession so the bridge call returns ``resp``."""
    return patch("aiohttp.ClientSession", lambda *a, **kw: _FakeSession(resp))


class TestReadWhatsappHistoryHttp:
    def test_read_history_parses_200(self):
        resp = _FakeResp(200, payload={"count": 2, "messages": [{"body": "hi"}, {"body": "yo"}]})
        with _patch_session(resp):
            result = asyncio.run(_read_whatsapp_history({"bridge_port": 3000}, "3569@s.whatsapp.net", 50))
        assert result["success"] is True
        assert result["platform"] == "whatsapp"
        assert result["chat_id"] == "3569@s.whatsapp.net"
        assert result["count"] == 2
        assert [m["body"] for m in result["messages"]] == ["hi", "yo"]

    def test_read_history_surfaces_non_200(self):
        resp = _FakeResp(503, text="bridge down")
        with _patch_session(resp):
            result = asyncio.run(_read_whatsapp_history({}, "x@lid", 10))
        assert "error" in result
        assert "503" in result["error"]

    def test_read_history_uses_configured_bridge_port(self):
        captured = {}

        class _PortSession(_FakeSession):
            def get(self, url, *args, **kwargs):
                captured["url"] = url
                return _AsyncCtx(_FakeResp(200, payload={"count": 0, "messages": []}))

        with patch("aiohttp.ClientSession", lambda *a, **kw: _PortSession(None)):
            asyncio.run(_read_whatsapp_history({"bridge_port": 4242}, "x@lid", 5))
        assert captured["url"] == "http://localhost:4242/history"


class TestListWhatsappChatsHttp:
    def test_list_chats_parses_200(self):
        chats = [{"jid": "x@lid", "name": "Craig", "messageCount": 12}]
        resp = _FakeResp(200, payload={"chats": chats})
        with _patch_session(resp):
            result = asyncio.run(_list_whatsapp_chats({"bridge_port": 3000}))
        assert result["success"] is True
        assert result["platform"] == "whatsapp"
        assert result["chats"] == chats

    def test_list_chats_surfaces_non_200(self):
        resp = _FakeResp(500, text="boom")
        with _patch_session(resp):
            result = asyncio.run(_list_whatsapp_chats({}))
        assert "error" in result


class TestReadWhatsappHistoryHandler:
    def _patches(self, payload):
        pconfig = SimpleNamespace(extra={"bridge_port": 3000})
        async_mock = AsyncMock(return_value=payload)
        return (
            patch("tools.send_message_tool._whatsapp_pconfig", return_value=(pconfig, None)),
            patch("tools.send_message_tool._read_whatsapp_history", async_mock),
            patch("model_tools._run_async", side_effect=lambda c: asyncio.run(c)),
        )

    def test_handler_returns_messages(self):
        payload = {"success": True, "count": 1, "messages": [{"body": "hi"}]}
        p_cfg, p_fn, p_run = self._patches(payload)
        with p_cfg, p_fn as async_mock, p_run:
            result = read_whatsapp_history_tool({"chat_id": "c@lid", "limit": 10})
        data = json.loads(result)
        assert data["success"] is True
        async_mock.assert_awaited_once_with({"bridge_port": 3000}, "c@lid", 10)

    def test_handler_requires_chat_id(self):
        result = read_whatsapp_history_tool({})
        assert "error" in json.loads(result)

    def test_handler_surfaces_config_error(self):
        err = '{"error": "not configured"}'
        with patch("tools.send_message_tool._whatsapp_pconfig", return_value=(None, err)):
            result = read_whatsapp_history_tool({"chat_id": "c@lid"})
        assert json.loads(result) == {"error": "not configured"}


class TestListWhatsappChatsHandler:
    def test_handler_returns_chats(self):
        pconfig = SimpleNamespace(extra={"bridge_port": 3000})
        async_mock = AsyncMock(return_value={"success": True, "chats": [{"jid": "c@lid"}]})
        with (
            patch("tools.send_message_tool._whatsapp_pconfig", return_value=(pconfig, None)),
            patch("tools.send_message_tool._list_whatsapp_chats", async_mock),
            patch("model_tools._run_async", side_effect=lambda c: asyncio.run(c)),
        ):
            result = list_whatsapp_chats_tool({})
        data = json.loads(result)
        assert data["success"] is True
        async_mock.assert_awaited_once_with({"bridge_port": 3000})

    def test_handler_surfaces_config_error(self):
        err = '{"error": "not configured"}'
        with patch("tools.send_message_tool._whatsapp_pconfig", return_value=(None, err)):
            result = list_whatsapp_chats_tool({})
        assert json.loads(result) == {"error": "not configured"}
