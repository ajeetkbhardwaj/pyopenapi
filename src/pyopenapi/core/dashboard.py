"""Local HTTP dashboard for live agent pipeline progress."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..utils import ensure_directory
from .progress import PROGRESS_FILENAME, build_dashboard_state, read_events


def _package_template_dir() -> Path | None:
    """Return the templates directory shipped inside the installed package."""

    try:
        from importlib.resources import files

        candidate = Path(str(files("pyopenapi") / "templates"))
        return candidate if candidate.is_dir() else None
    except Exception:
        return None


def _template_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    candidates: list[Path | None] = [
        Path(os.environ["PYOPENAPI_TEMPLATE_DIR"]).expanduser()
        if os.getenv("PYOPENAPI_TEMPLATE_DIR")
        else None,
        _package_template_dir(),  # installed wheel / package data
        here.parents[1] / "templates",  # src/pyopenapi/templates
        here.parents[3] / "templates",  # pyopenapi/templates (source checkout)
        here.parents[4] / "common" / "templates",  # knowl-apidocs/common/templates
        here.parents[3] / "common" / "templates",
    ]
    seen: set[Path] = set()
    dirs: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        dirs.append(resolved)
    return dirs


def _find_template(dirs: list[Path], name: str = "dashboard.html") -> Path | None:
    """Find a template in either ``<dir>/name`` or ``<dir>/stats/name``."""

    for directory in dirs:
        for candidate in (directory / name, directory / "stats" / name):
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


_TEMPLATE_DIRS = _template_dirs()
_TEMPLATE = _find_template(_TEMPLATE_DIRS)
_TEMPLATE_DIR = (
    _TEMPLATE.parent
    if _TEMPLATE is not None
    else (_TEMPLATE_DIRS[0] if _TEMPLATE_DIRS else Path(__file__).resolve().parents[3])
)


def _fallback_dashboard_html(stats: dict[str, Any]) -> str:
    """Minimal self-contained dashboard used when no template is available."""

    stats_json = json.dumps(stats, ensure_ascii=False, default=str)
    summary = stats.get("summary") or {}
    llm = stats.get("llm_stats") or {}
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pyopenapi dashboard</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:24px;background:#0f172a;color:#e2e8f0}}
h1{{font-size:22px;margin:0 0 4px}}p{{color:#94a3b8;margin:0 0 20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px}}
.label{{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.value{{font-size:26px;font-weight:600;margin-top:6px}}
pre{{background:#020617;border-radius:8px;padding:12px;overflow:auto;font-size:12px}}
</style>
</head>
<body>
<h1>pyopenapi agent dashboard</h1>
<p>{summary.get("project_name", "")} · {summary.get("framework", "")} · {summary.get("status", "unknown")}</p>
<div class="grid">
<div class="card"><div class="label">Endpoints</div><div class="value">{(stats.get("endpoints") or {}).get("total", 0)}</div></div>
<div class="card"><div class="label">Components</div><div class="value">{(stats.get("serializers") or {}).get("total", 0)}</div></div>
<div class="card"><div class="label">LLM calls</div><div class="value">{llm.get("total_calls", 0)}</div></div>
<div class="card"><div class="label">Total tokens</div><div class="value">{llm.get("total_tokens", 0)}</div></div>
<div class="card"><div class="label">Quality score</div><div class="value">{stats.get("quality_score", "N/A")}</div></div>
<div class="card"><div class="label">Duration</div><div class="value">{stats.get("duration_ms", 0)} ms</div></div>
</div>
<h2 style="font-size:16px;margin:24px 0 8px">Raw stats</h2>
<pre id="raw"></pre>
<script>
window.statsData = {stats_json};
document.getElementById("raw").textContent = JSON.stringify(window.statsData, null, 2);
</script>
</body>
</html>"""


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _agent_to_stats(progress_path: Path) -> dict[str, Any]:
    events = read_events(progress_path)
    state = build_dashboard_state(events)
    trace_path = progress_path.parent / "agent_trace.json"
    trace = _load_json(trace_path) or {}
    run = state.get("run") or {}
    trace_meta = trace.get("metadata") or {}
    run_started = trace.get("started_at")
    run_completed = trace.get("completed_at")
    duration_ms = int(trace.get("duration_ms") or run.get("duration_ms") or 0)
    provider = trace.get("provider") or run.get("provider")
    model = trace.get("model") or run.get("model")
    framework = trace.get("framework") or run.get("framework")
    project_path = trace.get("project_path") or run.get("project_path") or "pyopenapi"
    analysis_id = trace.get("analysis_id") or run.get("analysis_id") or ""
    status = trace.get("status") or state.get("status") or "unknown"

    llm_calls: list[dict[str, Any]] = []
    raw_attempts = trace.get("attempts") or []
    if raw_attempts:
        for attempt in raw_attempts:
            if not isinstance(attempt, dict):
                continue
            stage = str(attempt.get("stage") or "unknown")
            valid = bool(attempt.get("valid", True))
            errors = [str(err) for err in (attempt.get("errors") or [])]
            total_tokens = int(attempt.get("total_tokens") or 0)
            prompt_tokens = int(attempt.get("prompt_tokens") or 0)
            completion_tokens = int(attempt.get("completion_tokens") or 0)
            llm_calls.append(
                {
                    "attempt_number": attempt.get("attempt"),
                    "call_type": f"agent_{stage}",
                    "stage": stage,
                    "item_id": attempt.get("item_id"),
                    "status": "success" if valid else "failure",
                    "duration_ms": int(attempt.get("latency_ms") or 0),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "tokens_used": total_tokens,
                    "reasoning_tokens": 0,
                    "cost_usd": float(attempt.get("cost_usd") or 0.0),
                    "has_reasoning_content": False,
                    "provider": attempt.get("provider") or provider,
                    "model": attempt.get("model") or model,
                    "prompt": _json_text(attempt.get("input_summary") or {})[:4000],
                    "response": _json_text(attempt.get("output_summary") or {})[:4000],
                    "error": "; ".join(errors) if errors else None,
                    "error_type": "validation" if errors else None,
                    "finish_reason": None,
                    "timestamp": run_started,
                    "confidence": attempt.get("confidence"),
                }
            )
    else:
        for idx, stage in enumerate(state.get("stages") or []):
            stage_name = str(stage.get("stage") or "")
            if not stage_name:
                continue
            llm_calls.append(
                {
                    "attempt_number": idx + 1,
                    "call_type": f"agent_{stage_name}",
                    "stage": stage_name,
                    "status": "success" if stage.get("status") == "completed" else stage.get("status"),
                    "duration_ms": int(stage.get("duration_ms") or 0),
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "tokens_used": 0,
                    "reasoning_tokens": 0,
                    "cost_usd": 0.0,
                    "has_reasoning_content": False,
                    "provider": provider,
                    "model": model,
                    "prompt": "",
                    "response": "",
                    "error": "; ".join(state.get("errors") or []) if stage.get("status") == "failed" else None,
                    "error_type": None,
                    "finish_reason": None,
                    "timestamp": stage.get("started_at"),
                }
            )

    total_prompt = sum(call["prompt_tokens"] for call in llm_calls)
    total_completion = sum(call["completion_tokens"] for call in llm_calls)
    total_tokens = sum(call["total_tokens"] for call in llm_calls)
    total_cost = sum(call["cost_usd"] for call in llm_calls)
    by_call_type: dict[str, dict[str, Any]] = {}
    for call in llm_calls:
        key = call["call_type"]
        entry = by_call_type.setdefault(
            key, {"calls": 0, "tokens": 0, "cost_usd": 0.0, "avg_duration_ms": 0.0, "_duration": 0}
        )
        entry["calls"] += 1
        entry["tokens"] += call["total_tokens"]
        entry["cost_usd"] += call["cost_usd"]
        entry["_duration"] += call["duration_ms"]
    for entry in by_call_type.values():
        entry["avg_duration_ms"] = entry.pop("_duration") / max(1, entry["calls"])

    items = state.get("items") or {}
    # Include trace-only items when the progress file is unavailable.
    for call in llm_calls:
        stage = call.get("stage")
        item_id = call.get("item_id")
        if stage not in {"endpoint", "component"} or not item_id:
            continue
        key = f"{stage}:{item_id}"
        if key not in items:
            items[key] = {
                "kind": stage,
                "id": item_id,
                "label": item_id,
                "status": "done" if call["status"] == "success" else "error",
                "confidence": call.get("confidence"),
                "error": call.get("error"),
            }

    status_map = {"done": "success", "error": "failed", "running": "running", "pending": "pending"}

    def build_entity(kind: str, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item.get("id") or "")
        stage = "endpoint" if kind == "endpoint" else "component"
        calls = [call for call in llm_calls if call.get("stage") == stage and call.get("item_id") == item_id]
        tokens = sum(call["total_tokens"] for call in calls)
        entity_status = status_map.get(str(item.get("status")), str(item.get("status") or "pending"))
        return {
            "entity_id": item_id,
            "entity_type": "endpoint" if kind == "endpoint" else "serializer",
            "status": entity_status,
            "duration_ms": sum(call["duration_ms"] for call in calls),
            "start_time": run_started,
            "end_time": run_completed if entity_status in {"success", "failed"} else None,
            "llm_requests": calls,
            "validation_attempts": [],
            "validation_retry_count": max(0, len(calls) - 1),
            "tags": [],
            "metadata": {"label": item.get("label") or item_id, "tokens": tokens},
            "error": item.get("error"),
            "error_type": "agent_error" if item.get("error") else None,
        }

    endpoint_entities = [
        build_entity("endpoint", item)
        for item in items.values()
        if item.get("kind") == "endpoint"
    ]
    serializer_entities = [
        build_entity("component", item)
        for item in items.values()
        if item.get("kind") == "component"
    ]

    def status_counts(entities: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"success": 0, "failed": 0, "running": 0, "pending": 0}
        for entity in entities:
            counts[entity["status"]] = counts.get(entity["status"], 0) + 1
        return counts

    retries = sum(entity["validation_retry_count"] for entity in endpoint_entities + serializer_entities)
    return {
        "repo_name": project_path,
        "run_id": analysis_id,
        "duration_ms": duration_ms,
        "summary": {
            "project_name": project_path,
            "run_timestamp": analysis_id,
            "status": status,
            "framework": framework,
            "provider": provider,
            "model": model,
            "quality_score": trace_meta.get("quality_score") or state.get("quality_score"),
        },
        "llm_stats": {
            "total_calls": len(llm_calls),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "average_latency_ms": sum(call["duration_ms"] for call in llm_calls) / max(1, len(llm_calls)),
            "by_call_type": by_call_type,
        },
        "total_reasoning_tokens": 0,
        "total_cost_usd": total_cost,
        "llm_calls": llm_calls,
        "validation_stats": {
            "total_validations": len(llm_calls),
            "passed": sum(1 for call in llm_calls if call["status"] == "success"),
            "failed": sum(1 for call in llm_calls if call["status"] != "success"),
            "retried": retries,
            "total_validation_retries": retries,
        },
        "endpoints": {
            "total": len(endpoint_entities),
            "entities": endpoint_entities,
            "status_counts": status_counts(endpoint_entities),
        },
        "serializers": {
            "total": len(serializer_entities),
            "entities": serializer_entities,
            "status_counts": status_counts(serializer_entities),
        },
        "agent_state": state,
        "agent_progress_path": str(progress_path),
        "agent_trace_path": str(trace_path) if trace else None,
        "spec_path": state.get("spec_path") or trace_meta.get("spec_path"),
        "quality_score": trace_meta.get("quality_score") or state.get("quality_score"),
    }


def _render_dashboard_html(stats: dict[str, Any]) -> str:
    template = _TEMPLATE if _TEMPLATE is not None and _TEMPLATE.is_file() else _find_template(_TEMPLATE_DIRS)
    if template is None:
        return _fallback_dashboard_html(stats)
    try:
        body = template.read_text(encoding="utf-8")
    except OSError:
        return _fallback_dashboard_html(stats)
    stats_json = json.dumps(stats, ensure_ascii=False, default=str)
    if "{/* STATS_DATA_JSON_PLACEHOLDER */}" in body:
        return body.replace("{/* STATS_DATA_JSON_PLACEHOLDER */}", stats_json)
    return body.replace(
        "window.statsData = {/* STATS_DATA_JSON_PLACEHOLDER */};",
        f"window.statsData = {stats_json};",
    )


def write_dashboard_report(
    output_dir: str | Path,
    progress_path: str | Path | None = None,
) -> Path | None:
    """Write a self-contained final dashboard report next to the agent outputs."""

    out = Path(output_dir).expanduser().resolve()
    ensure_directory(out)
    source = Path(progress_path) if progress_path else out / PROGRESS_FILENAME
    html = _render_dashboard_html(_agent_to_stats(source))
    report = out / "dashboard.html"
    report.write_text(html, encoding="utf-8")
    for asset in ("dashboard.css", "dashboard.js"):
        src = _TEMPLATE_DIR / asset
        if src.is_file():
            try:
                shutil.copyfile(src, out / asset)
            except Exception:
                pass
    return report


class DashboardServer:
    def __init__(
        self,
        output_dir: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        progress_path: str | Path | None = None,
        open_browser: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.host = host
        self.port = port
        self.progress_path = (
            Path(progress_path) if progress_path else self.output_dir / PROGRESS_FILENAME
        )
        self.open_browser = open_browser
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        progress_path = self.progress_path
        template_dirs = list(_TEMPLATE_DIRS)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _serve_static(self, path: str) -> bool:
                rel = path.lstrip("/")
                if rel.startswith("static/"):
                    rel = rel[len("static/"):]
                candidates = [directory / rel for directory in template_dirs]
                candidates.extend(directory / "stats" / rel for directory in template_dirs)
                for cand in candidates:
                    if not cand.is_file():
                        continue
                    try:
                        data = cand.read_bytes()
                    except Exception:
                        continue
                    ctype, _ = mimetypes.guess_type(str(cand))
                    self._send(200, ctype or "application/octet-stream", data)
                    return True
                return False

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path.startswith("/static/") or path.endswith((".css", ".js", ".png", ".jpg", ".svg", ".json")):
                    if self._serve_static(path):
                        return
                if path in {"/", "/index.html", "/agent", "/live"}:
                    body = _render_dashboard_html(_agent_to_stats(progress_path))
                    self._send(200, "text/html; charset=utf-8", body.encode("utf-8"))
                    return
                if path == "/api/state":
                    state = build_dashboard_state(read_events(progress_path))
                    self._send(
                        200,
                        "application/json; charset=utf-8",
                        json.dumps(state, ensure_ascii=False, default=str).encode(),
                    )
                    return
                if path in {"/api/stats", "/api/agent/state"}:
                    stats = _agent_to_stats(progress_path)
                    self._send(
                        200,
                        "application/json; charset=utf-8",
                        json.dumps(stats, ensure_ascii=False, default=str).encode(),
                    )
                    return
                if path == "/api/events":
                    events = read_events(progress_path)
                    body = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
                    self._send(200, "application/x-ndjson; charset=utf-8", body.encode())
                    return
                if path == "/health":
                    self._send(200, "application/json", b'{"ok": true}')
                    return
                # try static
                if self._serve_static(path):
                    return
                self._send(404, "text/plain; charset=utf-8", b"not found")

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        actual_port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://{self.host}:{actual_port}/"
        if self.open_browser:
            webbrowser.open(url)
        return url

    def wait(self) -> None:
        if self._thread is None:
            return
        try:
            while self._thread.is_alive():
                self._thread.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def serve_dashboard(
    output_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    progress_path: str | Path | None = None,
    open_browser: bool = False,
) -> None:
    server = DashboardServer(
        output_dir,
        host=host,
        port=port,
        progress_path=progress_path,
        open_browser=open_browser,
    )
    url = server.start()
    print(f"pyopenapi dashboard: {url}", flush=True)
    server.wait()
