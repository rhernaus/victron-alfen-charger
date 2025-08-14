import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger
from gi.repository import GLib

from .config_schema import get_config_schema


class DebugAccessLogger(AbstractAccessLogger):
    """Log HTTP access lines at DEBUG level instead of INFO."""

    def log(
        self, request: web.Request, response: web.StreamResponse, time: float
    ) -> None:  # noqa: D401
        try:
            remote = request.remote or "-"
            method = request.method
            path = str(request.rel_url)
            status = getattr(response, "status", 0)
            ua = request.headers.get("User-Agent", "-")
            self.logger.debug(
                '%s "%s %s" %s %.3f %s', remote, method, path, status, time, ua
            )
        except Exception as exc:  # pragma: no cover
            self.logger.debug("access log failed: %s", exc)


class WebServer:
    """Lightweight HTTP server for status and control of the charger.

    The server runs in its own asyncio event loop on a background thread.
    State-changing operations are scheduled on the GLib main loop to remain
    thread-safe with the driver's GLib-based lifecycle.
    """

    def __init__(
        self, driver: Any, host: Optional[str] = None, port: int = 8088
    ) -> None:
        self.driver = driver
        # Priority: explicit args -> config.web -> env -> default
        cfg_host = getattr(getattr(driver, "config", None), "web", None)
        cfg_host_val = getattr(cfg_host, "host", None)
        cfg_port_val = getattr(cfg_host, "port", None)
        env_host = os.getenv("ALFEN_WEB_HOST")
        env_port = os.getenv("ALFEN_WEB_PORT")
        self.host = host or cfg_host_val or env_host or "127.0.0.1"
        self.port = int(env_port or cfg_port_val or port)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.runner: Optional[web.AppRunner] = None
        self.thread: Optional[threading.Thread] = None
        self.access_logger = logging.getLogger("alfen_driver.http")
        self.access_logger.setLevel(logging.DEBUG)

    def _get_static_dir(self) -> Path:
        base_dir = Path(os.path.dirname(__file__)) / "webui"
        return base_dir

    async def _run_on_glib(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute a callable on the GLib main loop and await the result in asyncio."""
        if self.loop is None:
            raise RuntimeError("Event loop not initialized")

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        def _invoke() -> bool:
            try:
                result = func(*args, **kwargs)
                if self.loop is not None:
                    self.loop.call_soon_threadsafe(future.set_result, result)
            except Exception as exc:  # pragma: no cover
                if self.loop is not None:
                    self.loop.call_soon_threadsafe(future.set_exception, exc)
            return False

        GLib.idle_add(_invoke, priority=GLib.PRIORITY_DEFAULT)
        return await future

    async def handle_status(self, request: web.Request) -> web.Response:
        snapshot: Dict[str, Any]
        try:
            # Use a lock if available on the driver for snapshot consistency
            lock = getattr(self.driver, "status_lock", None)
            if lock is not None:
                with lock:
                    snapshot = dict(getattr(self.driver, "status_snapshot", {}) or {})
            else:
                snapshot = dict(getattr(self.driver, "status_snapshot", {}) or {})
        except Exception:
            snapshot = {}
        return web.json_response(snapshot)

    async def handle_get_schema(self, request: web.Request) -> web.Response:
        return web.json_response(get_config_schema())

    async def handle_get_config(self, request: web.Request) -> web.Response:
        # Get current config dict from driver
        cfg = await self._run_on_glib(self.driver.get_config_dict)
        return web.json_response(cfg)

    async def handle_put_config(self, request: web.Request) -> web.Response:
        # Accept full config document as JSON; validate and apply
        try:
            payload = await request.json()
            result = await self._run_on_glib(
                self.driver.apply_config_from_dict, payload
            )
            status = 200 if result.get("ok") else 400
            return web.json_response(result, status=status)
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    async def handle_set_mode(self, request: web.Request) -> web.Response:
        data = await request.json()
        mode = int(data.get("mode", 0))
        result = await self._run_on_glib(self.driver.mode_callback, "/Mode", mode)
        return web.json_response({"ok": bool(result)})

    async def handle_startstop(self, request: web.Request) -> web.Response:
        data = await request.json()
        enabled = bool(data.get("enabled", True))
        value = 1 if enabled else 0
        result = await self._run_on_glib(
            self.driver.startstop_callback, "/StartStop", value
        )
        return web.json_response({"ok": bool(result)})

    async def handle_set_current(self, request: web.Request) -> web.Response:
        data = await request.json()
        amps = float(data.get("amps", 6.0))
        result = await self._run_on_glib(
            self.driver.set_current_callback, "/SetCurrent", amps
        )
        return web.json_response({"ok": bool(result)})

    async def index(self, request: web.Request) -> web.Response:
        return web.Response(
            text="Alfen Charger Web UI. Visit /ui/", content_type="text/plain"
        )

    async def _create_app(self) -> web.Application:
        app = web.Application()

        # Simple CORS for JSON endpoints (local device UI usage)
        @web.middleware
        async def cors_mw(
            request: web.Request,
            handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        ) -> web.StreamResponse:
            response: web.StreamResponse = await handler(request)
            if request.path.startswith("/api/"):
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
                response.headers[
                    "Access-Control-Allow-Methods"
                ] = "GET,POST,PUT,OPTIONS"
            return response

        app.middlewares.append(cors_mw)

        app.add_routes(
            [
                web.get("/", self.index),
                web.get("/api/status", self.handle_status),
                web.get("/api/config/schema", self.handle_get_schema),
                web.get("/api/config", self.handle_get_config),
                web.put("/api/config", self.handle_put_config),
                web.post("/api/mode", self.handle_set_mode),
                web.post("/api/startstop", self.handle_startstop),
                web.post("/api/set_current", self.handle_set_current),
            ]
        )

        static_dir = self._get_static_dir()
        if static_dir.exists():

            async def _redirect_root(request: web.Request) -> web.Response:
                return web.HTTPFound("/ui/index.html")

            app.router.add_get("/ui", _redirect_root)
            app.router.add_get("/ui/", _redirect_root)
            app.router.add_static("/ui/", str(static_dir), show_index=False)
        return app

    async def _start_async(self) -> None:
        self.loop = asyncio.get_running_loop()
        app = await self._create_app()
        self.runner = web.AppRunner(
            app, access_log_class=DebugAccessLogger, access_log=self.access_logger
        )
        await self.runner.setup()
        site = web.TCPSite(self.runner, host=self.host, port=self.port)
        await site.start()

    async def _stop_async(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def start(self) -> None:
        if self.thread is not None:
            return

        def _thread_target() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._start_async())
            try:
                self.loop.run_forever()
            finally:  # pragma: no cover
                self.loop.run_until_complete(self._stop_async())
                self.loop.close()

        self.thread = threading.Thread(
            target=_thread_target, name="WebServer", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            self.thread = None


def start_web_server(
    driver: Any, host: Optional[str] = None, port: int = 8088
) -> WebServer:
    server = WebServer(driver, host=host, port=port)
    server.start()
    return server
