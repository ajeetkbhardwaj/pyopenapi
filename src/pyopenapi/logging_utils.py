"""Logging and lightweight observability helpers.

For the stdio transport, *nothing* may be written to stdout except MCP
protocol frames. All logging therefore goes to stderr.

Optional features (all off by default, backward compatible):

* JSON logs (``PYOPENAPI_LOG_JSON=true``) with a request/tool correlation id.
* A tiny in-process metrics registry exposed at ``/metrics`` in Prometheus text
  format and via the ``cache_stats``/tooling layer.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any, Final

_CONFIGURED: bool = False
_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

_REQUEST_ID: ContextVar[str | None] = ContextVar("pyopenapi_request_id", default=None)


# ------------------------------------------------------------------ request ids
def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(value: str | None) -> None:
    _REQUEST_ID.set(value)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


# ---------------------------------------------------------------------- logging
class _JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib API
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """Configure root logging to write to stderr exactly once."""

    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level.upper())
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(_JsonFormatter() if json_logs else logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Keep third-party libraries quiet unless the user asked for debug output.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "dulwich"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------------- metrics
_METRICS_LOCK = threading.Lock()
_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = {}


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))


def increment(name: str, value: float = 1.0, *, labels: dict[str, str] | None = None) -> None:
    """Increment a counter metric (thread-safe)."""

    key = (name, _labels_key(labels))
    with _METRICS_LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0.0) + value


def observe(name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
    """Record a histogram observation (count/sum/min/max)."""

    key = (name, _labels_key(labels))
    with _METRICS_LOCK:
        entry = _HISTOGRAMS.setdefault(
            key, {"count": 0.0, "sum": 0.0, "min": value, "max": value}
        )
        entry["count"] += 1
        entry["sum"] += value
        entry["min"] = min(entry["min"], value)
        entry["max"] = max(entry["max"], value)


def get_metrics_snapshot() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of all metrics."""

    with _METRICS_LOCK:
        counters = [
            {"name": name, "labels": dict(labels), "value": value}
            for (name, labels), value in sorted(_COUNTERS.items())
        ]
        histograms = [
            {
                "name": name,
                "labels": dict(labels),
                "count": stats["count"],
                "sum": stats["sum"],
                "min": stats["min"],
                "max": stats["max"],
                "avg": (stats["sum"] / stats["count"]) if stats["count"] else 0.0,
            }
            for (name, labels), stats in sorted(_HISTOGRAMS.items())
        ]
    return {"counters": counters, "histograms": histograms}


def reset_metrics() -> None:
    with _METRICS_LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()


def render_prometheus() -> str:
    """Render metrics in the Prometheus text exposition format."""

    def _labels(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        inner = ",".join(f'{k}="{v}"' for k, v in labels)
        return "{" + inner + "}"

    lines: list[str] = []
    with _METRICS_LOCK:
        for (name, labels), value in sorted(_COUNTERS.items()):
            lines.append(f"{name}{_labels(labels)} {value}")
        for (name, labels), stats in sorted(_HISTOGRAMS.items()):
            label_text = _labels(labels)
            lines.append(f"{name}_count{label_text} {stats['count']}")
            lines.append(f"{name}_sum{label_text} {stats['sum']}")
            lines.append(f"{name}_min{label_text} {stats['min']}")
            lines.append(f"{name}_max{label_text} {stats['max']}")
    return "\n".join(lines) + ("\n" if lines else "")
