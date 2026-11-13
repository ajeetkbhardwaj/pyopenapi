from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from pyopenapi.core.dashboard import (
    DashboardServer,
    _render_dashboard_html,
    write_dashboard_report,
)
from pyopenapi.core.progress import build_dashboard_state, read_events


def test_build_dashboard_state_aggregates_runs() -> None:
    events = [
        {"type": "run_start", "framework": "django", "provider": "openrouter", "model": "m"},
        {"type": "stage_start", "stage": "endpoint", "item_count": 1},
        {"type": "item_start", "kind": "endpoint", "id": "e1", "label": "GET /items/"},
        {"type": "item_done", "kind": "endpoint", "id": "e1", "label": "GET /items/", "confidence": 0.8},
        {"type": "item_error", "kind": "component", "id": "c1", "label": "Item", "error": "bad"},
        {"type": "stage_end", "stage": "endpoint", "success": 1, "failure": 0, "duration_ms": 12},
        {"type": "run_done", "quality_score": 0.9, "spec_path": "/tmp/openapi.yml", "duration_ms": 100},
    ]

    state = build_dashboard_state(events)

    assert state["status"] == "completed"
    assert state["counts"]["endpoint"] == {"total": 1, "done": 1, "error": 0, "running": 0}
    assert state["counts"]["component"] == {"total": 1, "done": 0, "error": 1, "running": 0}
    assert state["quality_score"] == 0.9
    assert state["errors"] == ["bad"]
    assert state["stages"][0]["status"] == "completed"


def test_dashboard_serves_state(tmp_path: Path) -> None:
    progress = tmp_path / "agent_progress.jsonl"
    progress.write_text(
        json.dumps({"type": "run_start", "framework": "flask"}) + "\n",
        encoding="utf-8",
    )
    server = DashboardServer(tmp_path, port=0)
    url = server.start()
    try:
        with urlopen(url + "api/state", timeout=5) as response:
            state = json.loads(response.read().decode("utf-8"))
        assert state["status"] == "running"
        assert state["run"]["framework"] == "flask"
        assert read_events(progress)[0]["type"] == "run_start"
    finally:
        server.stop()


def test_dashboard_root_renders_template(tmp_path: Path) -> None:
    progress = tmp_path / "agent_progress.jsonl"
    progress.write_text(
        json.dumps({"type": "run_start", "framework": "flask"}) + "\n",
        encoding="utf-8",
    )
    server = DashboardServer(tmp_path, port=0)
    url = server.start()
    try:
        with urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "STATS_DATA_JSON_PLACEHOLDER" not in body
        assert "window.statsData" in body
    finally:
        server.stop()


def test_write_dashboard_report_renders_stats(tmp_path: Path) -> None:
    progress = tmp_path / "agent_progress.jsonl"
    progress.write_text(
        json.dumps({"type": "run_start", "framework": "flask"}) + "\n",
        encoding="utf-8",
    )

    report = write_dashboard_report(tmp_path, progress)

    assert report is not None
    assert report.is_file()
    body = report.read_text(encoding="utf-8")
    assert "STATS_DATA_JSON_PLACEHOLDER" not in body
    assert "window.statsData" in body


def test_render_dashboard_html_without_template_uses_fallback(monkeypatch) -> None:
    from pyopenapi.core import dashboard

    monkeypatch.setattr(dashboard, "_TEMPLATE", None)
    monkeypatch.setattr(dashboard, "_TEMPLATE_DIRS", [])
    html = _render_dashboard_html({"summary": {"project_name": "demo"}, "duration_ms": 0})

    assert "pyopenapi agent dashboard" in html
    assert "window.statsData" in html
