"""HTTP transport hardening and operational endpoints.

The MCP SDK exposes SSE and Streamable HTTP ASGI apps but does not add
authentication, rate limiting, body-size limits, request timeouts or health /
metrics endpoints. This module wraps those apps with a single ASGI middleware
and mounts the operational routes alongside the MCP endpoints.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ..config import Settings
from ..logging_utils import get_metrics_snapshot, increment, observe, render_prometheus
from ..utils import human_bytes

_STARTED_AT = time.time()
_HEALTH_PATHS = {"/health", "/healthz", "/ready", "/readyz"}

_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = threading.Lock()


def _client_ip(scope: dict[str, Any], settings: Settings) -> str:
    client = scope.get("client") or ("unknown", 0)
    host = str(client[0])
    if settings.trusted_proxies and host in set(settings.trusted_proxies):
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return host


def _header(scope: dict[str, Any], name: str) -> str | None:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


def _authorized(scope: dict[str, Any], settings: Settings) -> bool:
    if not settings.api_key:
        return True
    header = _header(scope, "authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        token = (_header(scope, "x-api-key") or "").strip()
    return token == settings.api_key


async def _rate_limited(ip: str, settings: Settings) -> bool:
    if settings.rate_limit_per_minute <= 0:
        return False
    now = time.monotonic()
    window = 60.0
    capacity = max(1, settings.rate_limit_per_minute + max(0, settings.rate_limit_burst))
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, deque())
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= capacity:
            return True
        bucket.append(now)
        if len(_rate_buckets) > 10_000:  # pragma: no cover - defensive bound
            _rate_buckets.clear()
        return False


class HttpGuardMiddleware:
    """ASGI middleware adding auth, limits, timeouts and metrics."""

    def __init__(self, app, *, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_public = path in _HEALTH_PATHS
        method = scope.get("method", "GET")
        started = time.perf_counter()

        async def respond(status: int, payload: dict[str, Any], extra_headers: dict[str, str] | None = None):
            response = JSONResponse(payload, status_code=status)
            if extra_headers:
                response.headers.update(extra_headers)
            await response(scope, receive, send)

        if not is_public and not _authorized(scope, self.settings):
            increment("http_auth_failures_total")
            await respond(401, {"error": "unauthorized", "message": "Missing or invalid API key."})
            return

        if not is_public and await _rate_limited(_client_ip(scope, self.settings), self.settings):
            increment("http_rate_limited_total")
            await respond(
                429,
                {"error": "rate_limited", "message": "Too many requests; slow down."},
                {"Retry-After": "60"},
            )
            return

        content_length = _header(scope, "content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > self.settings.max_request_bytes:
                increment("http_request_too_large_total")
                await respond(
                    413,
                    {
                        "error": "request_too_large",
                        "message": (
                            f"Request body exceeds {human_bytes(self.settings.max_request_bytes)}."
                        ),
                    },
                )
                return

        body_messages: list[dict[str, Any]] = []
        received = 0
        max_bytes = self.settings.max_request_bytes
        too_large = False
        disconnected = False

        while True:
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if max_bytes and received > max_bytes:
                    too_large = True
                    # Drain remaining body so the connection stays well-defined.
                    while message.get("more_body", False):
                        message = await receive()
                        if message["type"] != "http.request":
                            break
                    break
                body_messages.append(message)
                if not message.get("more_body", False):
                    break
                continue
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            body_messages.append(message)

        if disconnected:
            return
        if too_large:
            increment("http_request_too_large_total")
            await respond(
                413,
                {
                    "error": "request_too_large",
                    "message": (
                        f"Request body exceeds {human_bytes(self.settings.max_request_bytes)}."
                    ),
                },
            )
            return

        replay_index = 0

        async def replay_receive() -> dict[str, Any]:
            nonlocal replay_index
            if replay_index < len(body_messages):
                message = body_messages[replay_index]
                replay_index += 1
                return message
            # After buffered request messages, forward live events (e.g. disconnect).
            return await receive()

        status_code = 500

        async def status_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            timeout = self.settings.request_timeout_seconds
            if timeout and timeout > 0:
                await asyncio.wait_for(
                    self.app(scope, replay_receive, status_send),
                    timeout=timeout,
                )
            else:
                await self.app(scope, replay_receive, status_send)
        except asyncio.TimeoutError:
            increment("http_timeouts_total")
            await respond(
                504,
                {
                    "error": "request_timeout",
                    "message": f"Request exceeded {self.settings.request_timeout_seconds:g}s.",
                },
            )
        finally:
            increment("http_requests_total", labels={"method": method, "status": str(status_code)})
            observe("http_request_duration_seconds", time.perf_counter() - started)


async def _health(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    cache_dir = settings.resolved_cache_dir()
    return JSONResponse(
        {
            "status": "ok",
            "service": "pyopenapi",
            "uptime_seconds": round(time.time() - _STARTED_AT, 1),
            "transport": settings.transport,
            "cache_dir": str(cache_dir),
            "openapi_version": settings.openapi_version,
        }
    )


async def _ready(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    cache_dir = settings.resolved_cache_dir()
    writable = cache_dir.is_dir() and _is_writable(cache_dir)
    status = "ready" if writable else "degraded"
    return JSONResponse(
        {"status": status, "cache_dir": str(cache_dir), "writable": writable},
        status_code=200 if writable else 503,
    )


def _is_writable(path) -> bool:
    probe = path / ".pyopenapi-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


async def _metrics(request: Request) -> Response:
    increment("http_requests_total", labels={"method": "GET", "status": "200"})
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


async def _metrics_json(request: Request) -> Response:
    return JSONResponse(get_metrics_snapshot())


def build_http_app(mcp, *, transport: str, settings: Settings) -> Starlette:
    """Return a Starlette app wrapping the MCP HTTP transport.

    The MCP Streamable HTTP app relies on its Starlette lifespan to start the
    session-manager task group. Mounting that app inside another Starlette app
    would skip the mounted app's lifespan, so we reuse its routes directly and
    run the session manager from the outer lifespan.
    """

    if transport == "sse":
        inner = mcp.sse_app()
        lifespan = None
    else:
        # A StreamableHTTPSessionManager can only be started once. Build a
        # fresh manager per app so repeated app construction (tests, reloads)
        # does not reuse an already-started manager.
        mcp._session_manager = None
        inner = mcp.streamable_http_app()
        manager = mcp.session_manager
        def lifespan(app):
            return manager.run()

    routes = []
    if settings.enable_health_endpoints:
        routes.extend(
            [
                Route("/health", _health),
                Route("/healthz", _health),
                Route("/ready", _ready),
                Route("/readyz", _ready),
            ]
        )
    if settings.enable_metrics_endpoint:
        routes.append(Route(settings.metrics_path, _metrics))
        routes.append(Route(f"{settings.metrics_path}.json", _metrics_json))
    routes.extend(inner.routes)

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(HttpGuardMiddleware, settings=settings)
    return app
