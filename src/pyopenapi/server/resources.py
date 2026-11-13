"""MCP resources exposing analyses and generated documents."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ..config import get_settings
from ..core.analysis import summarize_component, summarize_endpoint
from ..core.markdown import generate_markdown
from ..core.spec import build_openapi_from_analysis
from ..logging_utils import get_logger
from .deps import manager
from .instance import mcp

logger = get_logger("pyopenapi.resources")

_MAX_PAGE = 5000


def _page_bounds(offset: int, limit: int) -> tuple[int, int]:
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), _MAX_PAGE))
    return offset, limit


@mcp.resource("pyopenapi://frameworks", mime_type="application/json")
def frameworks_resource() -> str:
    """Supported frameworks and capabilities."""

    from .tools import _FRAMEWORKS

    return json.dumps({"frameworks": [item.model_dump() for item in _FRAMEWORKS]}, indent=2)


@mcp.resource("pyopenapi://analyses", mime_type="application/json")
def analyses_resource() -> str:
    """List cached analyses."""

    summaries = manager().list_analyses()
    return json.dumps({"count": len(summaries), "analyses": [s.model_dump() for s in summaries]}, indent=2)


@mcp.resource("pyopenapi://analysis/{analysis_id}", mime_type="application/json")
def analysis_resource(analysis_id: str) -> str:
    """Summary for a single analysis."""

    record = manager().load(analysis_id)
    return json.dumps(
        {"summary": record.summary.model_dump(), "metadata": record.metadata}, indent=2
    )


@mcp.resource("pyopenapi://analysis/{analysis_id}/endpoints", mime_type="application/json")
def endpoints_resource(analysis_id: str) -> str:
    """All endpoints for an analysis."""

    record = manager().load(analysis_id)
    endpoints = [summarize_endpoint(item).model_dump() for item in record.endpoints]
    return json.dumps({"analysis_id": analysis_id, "count": len(endpoints), "endpoints": endpoints}, indent=2)


@mcp.resource(
    "pyopenapi://analysis/{analysis_id}/endpoints/{offset}/{limit}",
    mime_type="application/json",
)
def endpoints_page_resource(analysis_id: str, offset: str, limit: str) -> str:
    """A page of endpoints for an analysis (bounded to 5000 per page)."""

    record = manager().load(analysis_id)
    start, page_size = _page_bounds(int(offset), int(limit))
    page = record.endpoints[start : start + page_size]
    endpoints = [summarize_endpoint(item).model_dump() for item in page]
    return json.dumps(
        {
            "analysis_id": analysis_id,
            "total": len(record.endpoints),
            "offset": start,
            "limit": page_size,
            "count": len(endpoints),
            "endpoints": endpoints,
        },
        indent=2,
    )


@mcp.resource("pyopenapi://analysis/{analysis_id}/components", mime_type="application/json")
def components_resource(analysis_id: str) -> str:
    """All schema components for an analysis."""

    record = manager().load(analysis_id)
    components = [
        summarize_component(name, raw).model_dump() for name, raw in record.components.items()
    ]
    return json.dumps(
        {"analysis_id": analysis_id, "count": len(components), "components": components}, indent=2
    )


@mcp.resource(
    "pyopenapi://analysis/{analysis_id}/components/{offset}/{limit}",
    mime_type="application/json",
)
def components_page_resource(analysis_id: str, offset: str, limit: str) -> str:
    """A page of schema components for an analysis (bounded to 5000 per page)."""

    record = manager().load(analysis_id)
    items = list(record.components.items())
    start, page_size = _page_bounds(int(offset), int(limit))
    page = items[start : start + page_size]
    components = [summarize_component(name, raw).model_dump() for name, raw in page]
    return json.dumps(
        {
            "analysis_id": analysis_id,
            "total": len(items),
            "offset": start,
            "limit": page_size,
            "count": len(components),
            "components": components,
        },
        indent=2,
    )


@mcp.resource("pyopenapi://analysis/{analysis_id}/openapi", mime_type="application/yaml")
def openapi_resource(analysis_id: str) -> str:
    """Static OpenAPI document for an analysis (configured OpenAPI version)."""

    record = manager().load(analysis_id)
    spec = build_openapi_from_analysis(record, openapi_version=get_settings().openapi_version)
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)


@mcp.resource("pyopenapi://analysis/{analysis_id}/markdown", mime_type="text/markdown")
def markdown_resource(analysis_id: str) -> str:
    """Markdown index generated from an analysis (multi-file layout)."""

    record = manager().load(analysis_id)
    spec = build_openapi_from_analysis(record)
    output_dir = Path(record.summary.output_dir) / "markdown"
    result = generate_markdown(spec, output_dir, layout="multi")
    index = Path(result.index_path) if result.index_path else output_dir / "index.md"
    return index.read_text(encoding="utf-8")


@mcp.resource("pyopenapi://analysis/{analysis_id}/warnings", mime_type="application/json")
def warnings_resource(analysis_id: str) -> str:
    """Warnings collected during analysis."""

    record = manager().load(analysis_id)
    return json.dumps(
        {"analysis_id": analysis_id, "warnings": record.summary.warnings}, indent=2
    )



@mcp.resource("pyopenapi://analysis/{analysis_id}/trace", mime_type="application/json")
def trace_resource(analysis_id: str) -> str:
    """Agent generation trace for an analysis (agent engine output)."""

    record = manager().load(analysis_id)
    trace_path = Path(record.summary.output_dir) / "output" / "agent_trace.json"
    if not trace_path.exists():
        return json.dumps(
            {
                "analysis_id": analysis_id,
                "ok": False,
                "error": "agent_trace.json not found",
                "hint": "Run generate_openapi with engine='agent' for this analysis.",
            },
            indent=2,
        )
    return trace_path.read_text(encoding="utf-8")


@mcp.resource("pyopenapi://analysis/{analysis_id}/progress", mime_type="application/json")
def progress_resource(analysis_id: str) -> str:
    """Current agent generation progress state for an analysis."""

    from ..core.progress import build_dashboard_state, read_events

    record = manager().load(analysis_id)
    progress_path = Path(record.summary.output_dir) / "output" / "agent_progress.jsonl"
    events = read_events(progress_path)
    state = build_dashboard_state(events)
    return json.dumps(
        {
            "analysis_id": analysis_id,
            "progress_path": str(progress_path),
            "event_count": len(events),
            "state": state,
        },
        indent=2,
    )

