"""Tests for the web server module."""

import asyncio
import json
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from alfen_driver.web import DebugAccessLogger, WebServer, start_web_server


class TestDebugAccessLogger:
    """Test the DebugAccessLogger class."""

    def test_log_success(self) -> None:
        """Test successful logging of HTTP request."""
        logger = Mock()
        access_logger = DebugAccessLogger(logger, None)

        request = make_mocked_request(
            "GET", "/test", headers={"User-Agent": "TestAgent"}
        )
        request.remote = "127.0.0.1"

        response = Mock()
        response.status = 200

        access_logger.log(request, response, 0.123)

        logger.debug.assert_called_once_with(
            '%s "%s %s" %s %.3f %s',
            "127.0.0.1", "GET", "/test", 200, 0.123, "TestAgent"
        )

    def test_log_no_user_agent(self) -> None:
        """Test logging when User-Agent header is missing."""
        logger = Mock()
        access_logger = DebugAccessLogger(logger, None)

        request = make_mocked_request("GET", "/test", headers={})
        request.remote = "127.0.0.1"

        response = Mock()
        response.status = 200

        access_logger.log(request, response, 0.123)

        logger.debug.assert_called_once_with(
            '%s "%s %s" %s %.3f %s',
            "127.0.0.1", "GET", "/test", 200, 0.123, "-"
        )

    def test_log_no_remote(self) -> None:
        """Test logging when remote address is None."""
        logger = Mock()
        access_logger = DebugAccessLogger(logger, None)

        request = make_mocked_request("GET", "/test", headers={})
        request.remote = None

        response = Mock()
        response.status = 200

        access_logger.log(request, response, 0.123)

        logger.debug.assert_called_once_with(
            '%s "%s %s" %s %.3f %s',
            "-", "GET", "/test", 200, 0.123, "-"
        )


class TestWebServer:
    """Test the WebServer class."""

    def test_init_default_values(self) -> None:
        """Test WebServer initialization with default values."""
        driver = Mock()
        server = WebServer(driver)

        assert server.driver == driver
        assert server.host == "127.0.0.1"
        assert server.port == 8088
        assert server.loop is None
        assert server.runner is None
        assert server.thread is None

    def test_init_with_explicit_args(self) -> None:
        """Test WebServer initialization with explicit arguments."""
        driver = Mock()
        server = WebServer(driver, host="0.0.0.0", port=9000)

        assert server.host == "0.0.0.0"
        assert server.port == 9000

    def test_init_with_config(self) -> None:
        """Test WebServer initialization with config values."""
        driver = Mock()
        driver.config = Mock()
        driver.config.web = Mock()
        driver.config.web.host = "192.168.1.1"
        driver.config.web.port = 8080

        server = WebServer(driver)

        assert server.host == "192.168.1.1"
        assert server.port == 8080

    def test_init_with_env_vars(self) -> None:
        """Test WebServer initialization with environment variables."""
        driver = Mock()

        with patch.dict(
            os.environ, {"ALFEN_WEB_HOST": "10.0.0.1", "ALFEN_WEB_PORT": "8090"}
        ):
            server = WebServer(driver)

        assert server.host == "10.0.0.1"
        assert server.port == 8090

    def test_init_priority(self) -> None:
        """Test WebServer initialization priority: args > config > env > default."""
        driver = Mock()
        driver.config = Mock()
        driver.config.web = Mock()
        driver.config.web.host = "192.168.1.1"
        driver.config.web.port = 8080

        with patch.dict(
            os.environ, {"ALFEN_WEB_HOST": "10.0.0.1", "ALFEN_WEB_PORT": "8090"}
        ):
            # Explicit args should take priority
            server = WebServer(driver, host="172.16.0.1", port=9000)

        assert server.host == "172.16.0.1"
        assert server.port == 9000

    def test_get_static_dir(self) -> None:
        """Test _get_static_dir returns correct path."""
        driver = Mock()
        server = WebServer(driver)

        static_dir = server._get_static_dir()
        assert isinstance(static_dir, Path)
        assert static_dir.name == "webui"

    @pytest.mark.asyncio
    async def test_run_on_glib_success(self) -> None:
        """Test _run_on_glib successful execution."""
        driver = Mock()
        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        # Mock GLib.idle_add to immediately call the function
        def mock_idle_add(func, priority=None):
            return func()

        test_func = Mock(return_value="test_result")

        with patch("alfen_driver.web.GLib.idle_add", mock_idle_add):
            result = await server._run_on_glib(test_func, "arg1", kwarg1="value1")

        assert result == "test_result"
        test_func.assert_called_once_with("arg1", kwarg1="value1")

    @pytest.mark.asyncio
    async def test_run_on_glib_no_loop(self) -> None:
        """Test _run_on_glib raises RuntimeError when loop is None."""
        driver = Mock()
        server = WebServer(driver)
        server.loop = None

        with pytest.raises(RuntimeError, match="Event loop not initialized"):
            await server._run_on_glib(lambda: None)

    @pytest.mark.asyncio
    async def test_handle_status_with_lock(self) -> None:
        """Test handle_status with status_lock."""
        driver = Mock()
        driver.status_lock = MagicMock()
        driver.status_snapshot = {"key": "value", "number": 42}

        server = WebServer(driver)
        request = make_mocked_request("GET", "/api/status")

        response = await server.handle_status(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"key": "value", "number": 42}
        driver.status_lock.__enter__.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_status_without_lock(self) -> None:
        """Test handle_status without status_lock."""
        driver = Mock()
        driver.status_lock = None
        driver.status_snapshot = {"key": "value"}

        server = WebServer(driver)
        request = make_mocked_request("GET", "/api/status")

        response = await server.handle_status(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_handle_status_exception(self) -> None:
        """Test handle_status handles exceptions gracefully."""
        driver = Mock()
        driver.status_snapshot = Mock(side_effect=Exception("Test error"))

        server = WebServer(driver)
        request = make_mocked_request("GET", "/api/status")

        response = await server.handle_status(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {}

    @pytest.mark.asyncio
    async def test_handle_get_schema(self) -> None:
        """Test handle_get_schema returns config schema."""
        driver = Mock()
        server = WebServer(driver)
        request = make_mocked_request("GET", "/api/config/schema")

        with patch(
            "alfen_driver.web.get_config_schema", return_value={"test": "schema"}
        ):
            response = await server.handle_get_schema(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"test": "schema"}

    @pytest.mark.asyncio
    async def test_handle_get_config(self) -> None:
        """Test handle_get_config returns current config."""
        driver = Mock()
        driver.get_config_dict = Mock(return_value={"config": "data"})

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()
        request = make_mocked_request("GET", "/api/config")

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"config": "data"}
            response = await server.handle_get_config(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"config": "data"}
        mock_run.assert_called_once_with(driver.get_config_dict)

    @pytest.mark.asyncio
    async def test_handle_put_config_success(self) -> None:
        """Test handle_put_config with valid config."""
        driver = Mock()
        driver.apply_config_from_dict = Mock(return_value={"ok": True})

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        request = make_mocked_request("PUT", "/api/config", json={"new": "config"})

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"ok": True}
            response = await server.handle_put_config(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"ok": True}
        mock_run.assert_called_once_with(
            driver.apply_config_from_dict, {"new": "config"}
        )

    @pytest.mark.asyncio
    async def test_handle_put_config_validation_error(self) -> None:
        """Test handle_put_config with validation error."""
        driver = Mock()
        driver.apply_config_from_dict = Mock(
            return_value={"ok": False, "error": "Invalid value"}
        )

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        request = make_mocked_request("PUT", "/api/config", json={"bad": "config"})

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"ok": False, "error": "Invalid value"}
            response = await server.handle_put_config(request)

        assert response.status == 400
        data = json.loads(response.text)
        assert data == {"ok": False, "error": "Invalid value"}

    @pytest.mark.asyncio
    async def test_handle_put_config_invalid_json(self) -> None:
        """Test handle_put_config with invalid JSON."""
        driver = Mock()
        server = WebServer(driver)

        request = make_mocked_request("PUT", "/api/config")
        request.json = AsyncMock(side_effect=json.JSONDecodeError("msg", "doc", 0))

        response = await server.handle_put_config(request)

        assert response.status == 400
        data = json.loads(response.text)
        assert data == {"ok": False, "error": "Invalid JSON"}

    @pytest.mark.asyncio
    async def test_handle_set_mode(self) -> None:
        """Test handle_set_mode."""
        driver = Mock()
        driver.mode_callback = Mock(return_value=True)

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        request = make_mocked_request("POST", "/api/mode", json={"mode": 2})

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = True
            response = await server.handle_set_mode(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"ok": True}
        mock_run.assert_called_once_with(driver.mode_callback, "/Mode", 2)

    @pytest.mark.asyncio
    async def test_handle_startstop_enable(self) -> None:
        """Test handle_startstop to enable charging."""
        driver = Mock()
        driver.startstop_callback = Mock(return_value=True)

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        request = make_mocked_request("POST", "/api/startstop", json={"enabled": True})

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = True
            response = await server.handle_startstop(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"ok": True}
        mock_run.assert_called_once_with(driver.startstop_callback, "/StartStop", 1)

    @pytest.mark.asyncio
    async def test_handle_startstop_disable(self) -> None:
        """Test handle_startstop to disable charging."""
        driver = Mock()
        driver.startstop_callback = Mock(return_value=True)

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        request = make_mocked_request("POST", "/api/startstop", json={"enabled": False})

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = True
            response = await server.handle_startstop(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"ok": True}
        mock_run.assert_called_once_with(driver.startstop_callback, "/StartStop", 0)

    @pytest.mark.asyncio
    async def test_handle_set_current(self) -> None:
        """Test handle_set_current."""
        driver = Mock()
        driver.set_current_callback = Mock(return_value=True)

        server = WebServer(driver)
        server.loop = asyncio.get_running_loop()

        request = make_mocked_request("POST", "/api/set_current", json={"amps": 16.5})

        with patch.object(server, "_run_on_glib", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = True
            response = await server.handle_set_current(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data == {"ok": True}
        mock_run.assert_called_once_with(
            driver.set_current_callback, "/SetCurrent", 16.5
        )

    @pytest.mark.asyncio
    async def test_index(self) -> None:
        """Test index handler."""
        driver = Mock()
        server = WebServer(driver)
        request = make_mocked_request("GET", "/")

        response = await server.index(request)

        assert response.status == 200
        assert response.text == "Alfen Charger Web UI. Visit /ui/"
        assert response.content_type == "text/plain"

    @pytest.mark.asyncio
    async def test_create_app_with_static_dir(self) -> None:
        """Test _create_app with static directory present."""
        driver = Mock()
        server = WebServer(driver)

        with patch.object(server, "_get_static_dir") as mock_get_dir:
            mock_dir = MagicMock()
            mock_dir.exists.return_value = True
            mock_get_dir.return_value = mock_dir

            app = await server._create_app()

        # Check routes are registered
        routes = [route.resource.canonical for route in app.router.routes()]
        assert "/" in routes
        assert "/api/status" in routes
        assert "/api/config/schema" in routes
        assert "/api/config" in routes
        assert "/api/mode" in routes
        assert "/api/startstop" in routes
        assert "/api/set_current" in routes
        assert "/ui" in routes
        assert "/ui/" in routes

    @pytest.mark.asyncio
    async def test_create_app_without_static_dir(self) -> None:
        """Test _create_app without static directory."""
        driver = Mock()
        server = WebServer(driver)

        with patch.object(server, "_get_static_dir") as mock_get_dir:
            mock_dir = MagicMock()
            mock_dir.exists.return_value = False
            mock_get_dir.return_value = mock_dir

            app = await server._create_app()

        # Static routes should not be registered
        routes = [route.resource.canonical for route in app.router.routes()]
        assert "/ui" not in routes
        assert "/ui/" not in routes

    @pytest.mark.asyncio
    async def test_cors_middleware(self) -> None:
        """Test CORS middleware for API endpoints."""
        driver = Mock()
        server = WebServer(driver)

        app = await server._create_app()

        # Create a test handler
        async def test_handler(request):
            return web.Response(text="test")

        # Get the CORS middleware
        cors_mw = app.middlewares[0]

        # Test API endpoint
        request = make_mocked_request("GET", "/api/test")
        response = await cors_mw(request, test_handler)

        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert response.headers["Access-Control-Allow-Headers"] == "Content-Type"
        assert (
            response.headers["Access-Control-Allow-Methods"]
            == "GET,POST,PUT,OPTIONS"
        )

        # Test non-API endpoint
        request = make_mocked_request("GET", "/other")
        response = await cors_mw(request, test_handler)

        assert "Access-Control-Allow-Origin" not in response.headers

    def test_start_stop(self) -> None:
        """Test start and stop methods."""
        driver = Mock()
        server = WebServer(driver)

        # Mock thread and event loop
        mock_thread = Mock()
        mock_loop = Mock()

        with patch("threading.Thread", return_value=mock_thread):
            with patch("asyncio.new_event_loop", return_value=mock_loop):
                # Test start
                server.start()

                assert server.thread == mock_thread
                mock_thread.start.assert_called_once()

                # Test start when already running (should do nothing)
                server.start()
                assert mock_thread.start.call_count == 1

                # Test stop
                server.loop = mock_loop
                server.stop()

                mock_loop.call_soon_threadsafe.assert_called_once_with(mock_loop.stop)
                mock_thread.join.assert_called_once_with(timeout=2.0)
                assert server.thread is None

    def test_stop_without_loop(self) -> None:
        """Test stop when loop is None."""
        driver = Mock()
        server = WebServer(driver)
        server.thread = Mock()
        server.loop = None

        server.stop()

        server.thread.join.assert_called_once_with(timeout=2.0)


def test_start_web_server() -> None:
    """Test start_web_server helper function."""
    driver = Mock()

    with patch.object(WebServer, "start") as mock_start:
        server = start_web_server(driver, host="0.0.0.0", port=9000)

    assert isinstance(server, WebServer)
    assert server.driver == driver
    assert server.host == "0.0.0.0"
    assert server.port == 9000
    mock_start.assert_called_once()
