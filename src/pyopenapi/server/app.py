"""Server application wiring and entry point."""

from __future__ import annotations

from ..config import get_settings
from ..logging_utils import configure_logging
from ..utils import install_stdout_guard
from .instance import mcp

# Importing these modules registers tools and resources on the shared instance.
from . import resources as _resources  # noqa: F401,E402
from . import tools as _tools  # noqa: F401,E402

__all__ = ["mcp", "run"]


def run(transport: str | None = None, host: str | None = None, port: int | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)

    if host:
        mcp.settings.host = host
    if port:
        mcp.settings.port = port

    chosen = transport or settings.transport

    if chosen == "stdio":
        # Nothing may be written to stdout except MCP frames.
        install_stdout_guard()
        mcp.run(transport="stdio")
        return

    # HTTP transports: wrap the MCP ASGI app with auth/limits/health/metrics.
    import uvicorn

    from .http import build_http_app

    app = build_http_app(mcp, transport=chosen, settings=settings)
    uvicorn.run(
        app,
        host=host or settings.host,
        port=port or settings.port,
        log_level=settings.log_level.lower(),
    )
