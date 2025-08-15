import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from alfen_driver.web import WebServer


@pytest.mark.asyncio
async def test_handle_status_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock driver with status snapshot
    driver = MagicMock()
    driver.status_snapshot = {"a": 1}

    # Avoid GLib usage in these handlers
    server = WebServer(driver)

    # handle_status
    req = MagicMock(spec=web.Request)
    resp = await server.handle_status(req)
    assert isinstance(resp, web.Response)
    assert resp.status == 200

    data = json.loads(resp.text)
    assert data == {"a": 1}

    # handle_get_schema
    resp2 = await server.handle_get_schema(req)
    assert resp2.status == 200
    schema = json.loads(resp2.text)
    assert isinstance(schema, dict)
    assert "sections" in schema


@pytest.mark.asyncio
async def test_config_handlers_call_glib(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock GLib to run callbacks immediately by calling function synchronously
    class FakeGLib:
        PRIORITY_DEFAULT = 0

        @staticmethod
        def idle_add(fn, priority=0):
            # Call immediately
            fn()
            return True

    monkeypatch.setattr("alfen_driver.web.GLib", FakeGLib)

    driver = MagicMock()
    driver.get_config_dict = MagicMock(return_value={"ok": True})
    driver.apply_config_from_dict = MagicMock(return_value={"ok": True})
    driver.mode_callback = MagicMock(return_value=True)
    driver.startstop_callback = MagicMock(return_value=True)
    driver.set_current_callback = MagicMock(return_value=True)

    server = WebServer(driver)
    # Provide an event loop so _run_on_glib does not raise
    server.loop = asyncio.get_running_loop()

    # handle_get_config
    resp = await server.handle_get_config(MagicMock(spec=web.Request))
    assert resp.status == 200
    cfg = json.loads(resp.text)
    assert cfg == {"ok": True}

    # handle_put_config with valid JSON
    class FakeReq:
        async def json(self):
            return {"x": 1}

    resp2 = await server.handle_put_config(FakeReq())
    assert resp2.status == 200
    payload = json.loads(resp2.text)
    assert payload == {"ok": True}

    # handle_set_mode
    class ModeReq:
        async def json(self):
            return {"mode": 2}

    r3 = await server.handle_set_mode(ModeReq())
    assert json.loads(r3.text) == {"ok": True}

    # handle_startstop
    class SSReq:
        async def json(self):
            return {"enabled": False}

    r4 = await server.handle_startstop(SSReq())
    assert json.loads(r4.text) == {"ok": True}

    # handle_set_current
    class CurReq:
        async def json(self):
            return {"amps": 10.5}

    r5 = await server.handle_set_current(CurReq())
    assert json.loads(r5.text) == {"ok": True}


@pytest.mark.asyncio
async def test_put_config_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use real request handling by calling handler with object whose json() raises JSONDecodeError
    driver = MagicMock()
    server = WebServer(driver)
    server.loop = asyncio.get_running_loop()

    class BadReq:
        async def json(self):
            raise json.JSONDecodeError("bad", "{}", 0)

    resp = await server.handle_put_config(BadReq())
    assert resp.status == 400
    assert json.loads(resp.text)["ok"] is False
