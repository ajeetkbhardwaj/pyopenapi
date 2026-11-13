"""Live progress events for the agent pipeline and dashboard.

The pipeline appends JSONL events to ``agent_progress.jsonl`` in the output
directory. The dashboard reads that file, so it can watch a run from a separate
terminal without coupling the generation process to an HTTP server.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..utils import ensure_directory, utc_now_iso

PROGRESS_FILENAME = "agent_progress.jsonl"


class ProgressWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        ensure_directory(self.path.parent)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, **data: Any) -> None:
        payload = {"ts": utc_now_iso(), "type": event_type, **data}
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def build_dashboard_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "status": "waiting",
        "run": {},
        "stages": [],
        "items": {},
        "counts": {
            "endpoint": {"total": 0, "done": 0, "error": 0, "running": 0},
            "component": {"total": 0, "done": 0, "error": 0, "running": 0},
        },
        "errors": [],
        "quality_score": None,
        "spec_path": None,
        "last_event": None,
        "event_count": len(events),
    }
    stage_map: dict[str, dict[str, Any]] = {}

    for event in events:
        event_type = str(event.get("type") or "")
        state["last_event"] = event

        if event_type == "run_start":
            state["status"] = "running"
            state["run"] = {
                key: event.get(key)
                for key in (
                    "analysis_id",
                    "project_path",
                    "framework",
                    "provider",
                    "model",
                    "openapi_version",
                )
            }
        elif event_type == "stage_start":
            stage = str(event.get("stage") or "")
            entry = {
                "stage": stage,
                "status": "running",
                "item_count": int(event.get("item_count") or 0),
                "success": 0,
                "failure": 0,
                "duration_ms": 0,
                "started_at": event.get("ts"),
            }
            stage_map[stage] = entry
            state["stages"].append(entry)
        elif event_type == "stage_end":
            stage = str(event.get("stage") or "")
            entry = stage_map.setdefault(
                stage,
                {
                    "stage": stage,
                    "item_count": 0,
                    "success": 0,
                    "failure": 0,
                },
            )
            entry["status"] = "failed" if int(event.get("failure") or 0) else "completed"
            entry["success"] = int(event.get("success") or 0)
            entry["failure"] = int(event.get("failure") or 0)
            entry["duration_ms"] = int(event.get("duration_ms") or 0)
            entry["completed_at"] = event.get("ts")
        elif event_type in {"item_start", "item_done", "item_error"}:
            kind = str(event.get("kind") or "item")
            item_id = str(event.get("id") or "")
            key = f"{kind}:{item_id}"
            item = state["items"].setdefault(
                key,
                {
                    "kind": kind,
                    "id": item_id,
                    "label": event.get("label") or item_id,
                    "status": "pending",
                    "confidence": None,
                    "error": None,
                },
            )
            item["label"] = event.get("label") or item["label"]
            if event_type == "item_start":
                item["status"] = "running"
            elif event_type == "item_done":
                item["status"] = "done"
                item["confidence"] = event.get("confidence")
            else:
                item["status"] = "error"
                item["error"] = event.get("error")
        elif event_type == "run_done":
            state["status"] = "completed"
            state["quality_score"] = event.get("quality_score")
            state["spec_path"] = event.get("spec_path")
            state["run"]["duration_ms"] = event.get("duration_ms")
            state["run"]["usage"] = event.get("usage")
        elif event_type == "run_error":
            state["status"] = "failed"

        if event_type in {"item_error", "run_error"}:
            message = event.get("error")
            if message:
                state["errors"].append(str(message))

    for item in state["items"].values():
        kind = item["kind"]
        bucket = state["counts"].setdefault(
            kind, {"total": 0, "done": 0, "error": 0, "running": 0}
        )
        bucket["total"] += 1
        status = item["status"]
        if status in {"done", "error", "running"}:
            bucket[status] += 1

    return state
