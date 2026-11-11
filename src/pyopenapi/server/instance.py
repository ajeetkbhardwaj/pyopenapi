"""FastMCP server instance."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..config import get_settings
from ..logging_utils import configure_logging

_settings = get_settings()
configure_logging(_settings.log_level, json_logs=_settings.log_json)

INSTRUCTIONS = """
pyopenapi turns Python web-framework source code into OpenAPI 3.0/3.1 specs and
Markdown documentation, and exposes the results over the Model Context Protocol.

Supported frameworks: Django (stable), Flask (preview), FastAPI (preview).

Typical workflow:
1. detect_framework(project_path) - optional; analyze_project auto-detects.
2. analyze_project(project_path) - returns an analysis_id and counts.
3. extract_endpoints(analysis_id) / extract_components(analysis_id) - inspect.
4. generate_openapi(analysis_id) - static by default; engine='nvidia',
   'lmstudio', 'llamacpp' or 'ollama' to enrich summaries/descriptions with an
   OpenAI-compatible provider. engine='llm' is the legacy Django-only pipeline.
   Set openapi_version='3.1.0' for JSON Schema 2020-12 output.
5. generate_markdown(analysis_id or spec_path) - deterministic Markdown docs.
6. validate_openapi(analysis_id or spec_path) - structural checks.
7. merge_specs / diff_specs - combine specs and detect breaking changes.
8. cache_stats / cleanup_cache - inspect and prune cached analyses.
9. analyze_workspace(workspace_path) - analyze and optionally merge many projects.

Everything is cached under the configured cache directory. Nothing is written to
stdout except MCP protocol frames.
""".strip()

mcp = FastMCP(
    name="pyopenapi",
    instructions=INSTRUCTIONS,
    host=_settings.host,
    port=_settings.port,
    log_level=_settings.log_level,
)
