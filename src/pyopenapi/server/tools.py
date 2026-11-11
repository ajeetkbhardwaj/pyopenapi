"""MCP tools for pyopenapi."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from pydantic import Field

from ..config import get_settings
from ..core.analysis import filter_endpoints, summarize_component, summarize_endpoint
from ..core.markdown import generate_markdown as _generate_markdown
from ..core.spec import (
    build_openapi_from_analysis,
    diff_specs as _diff_specs,
    generate_openapi as _generate_openapi,
    merge_specs as _merge_specs,
    write_spec,
)
from ..core.llm import llm_available
from ..core.validation import load_spec, validate_spec
from ..errors import PyOpenAPIError
from ..logging_utils import get_logger
from ..models import (
    AnalysisList,
    ComponentInfo,
    EndpointInfo,
    FrameworkInfo,
    MarkdownResult,
    ValidationReport,
    WorkspaceProject,
    WorkspaceResult,
)
from .deps import manager
from .instance import mcp

logger = get_logger("pyopenapi.tools")

FrameworkName = Literal["auto", "django", "flask", "fastapi"]
EngineName = Literal[
    "auto", "static", "nvidia", "lmstudio", "llamacpp", "ollama", "openai-compatible", "llm"
]
FormatName = Literal["yml", "yaml", "json"]
LayoutName = Literal["multi", "single"]
OpenAPIVersion = Literal["3.0.3", "3.1.0"]


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _fail(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, PyOpenAPIError):
        data = exc.to_dict()
    else:
        data = {"error": type(exc).__name__, "message": str(exc)}
    logger.warning("Tool error: %s", data)
    return {"ok": False, **data}


async def _progress(ctx: Context | None, current: float, total: float, message: str) -> None:
    if ctx is None:
        return
    try:
        await ctx.report_progress(current, total=total, message=message)
    except Exception:  # progress is best-effort and must never fail a tool
        pass


_FRAMEWORKS: list[FrameworkInfo] = [
    FrameworkInfo(
        name="django",
        display_name="Django / Django REST Framework",
        status="stable",
        analyzer="AST + dynamic URLconf + LLM pipeline",
        capabilities=[
            "ViewSet and APIView endpoints",
            "Serializer and Model schema components",
            "DRF routers, @action, pagination/auth settings",
            "LLM-enhanced OpenAPI generation",
        ],
    ),
    FrameworkInfo(
        name="flask",
        display_name="Flask / Flask-RESTful / flask-smorest",
        status="preview",
        analyzer="static AST",
        capabilities=[
            "Blueprint and app routes",
            "add_url_rule / MethodView",
            "flask_restful add_resource",
            "flask-smorest @bp.arguments/@bp.response",
            "Marshmallow schema components",
        ],
        notes="Static extraction; LLM generation not yet wired for Flask.",
    ),
    FrameworkInfo(
        name="fastapi",
        display_name="FastAPI",
        status="preview",
        analyzer="static AST",
        capabilities=[
            "APIRouter and app routes",
            "include_router prefixes",
            "Pydantic model components",
            "Query/Path/Header/Cookie/Form/File parameters",
            "Security schemes and dependencies",
        ],
        notes="Static extraction; runtime FastAPI OpenAPI export will be added.",
    ),
]


@mcp.tool()
def list_frameworks() -> dict[str, Any]:
    """List supported frameworks, their status and capabilities."""

    try:
        return _ok(frameworks=[item.model_dump() for item in _FRAMEWORKS], default="auto")
    except Exception as exc:  # pragma: no cover
        return _fail(exc)


@mcp.tool()
async def detect_framework(project_path: str) -> dict[str, Any]:
    """Detect the web framework used by a Python project.

    Args:
        project_path: Local project path or GitHub repository URL.
    """

    try:
        from ..detection import detect_framework as _detect
        from ..source import resolve_project_source

        settings = get_settings()
        resolved = await asyncio.to_thread(resolve_project_source, project_path, settings)
        result = await asyncio.to_thread(
            _detect,
            resolved,
            max_files=settings.max_scan_files,
            max_file_bytes=settings.max_file_bytes,
        )
        return _ok(detection=result.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def analyze_project(
    project_path: str,
    framework: FrameworkName = "auto",
    use_dynamic: bool = False,
    django_settings_module: str | None = None,
    django_settings_file: str | None = None,
    django_urls_file: str | None = None,
    include_endpoints: bool = True,
    include_components: bool = True,
    max_files: int | None = None,
    max_file_bytes: int | None = None,
    force: bool = False,
    refresh_repo: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Analyze a project and cache endpoints and schema components.

    Args:
        project_path: Local project path or GitHub repository URL.
        framework: "auto", "django", "flask" or "fastapi".
        use_dynamic: Django only. Run the runtime URLconf extractor (requires the
            project's dependencies to be importable by the server interpreter).
            Defaults to static analysis for stability.
        django_settings_module: Dotted settings module, e.g. "myproject.settings".
        django_settings_file: Path to settings.py (alternative to the module).
        django_urls_file: Path to the root urls.py.
        include_endpoints: Extract endpoints (default true).
        include_components: Extract schema components (default true).
        max_files: Override the file scan limit for this analysis (bounded).
        max_file_bytes: Override the per-file size limit for this analysis.
        force: Re-run analysis even if a cached result exists.
        refresh_repo: Re-clone a GitHub repository URL before analysis.
    """

    try:
        await _progress(ctx, 0, 2, "Resolving project source")
        record = await asyncio.to_thread(
            manager().analyze,
            project_path,
            framework,
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
            include_endpoints=include_endpoints,
            include_components=include_components,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            force=force,
            refresh_repo=refresh_repo,
        )
        await _progress(ctx, 2, 2, "Analysis complete")
        return _ok(summary=record.summary.model_dump(), metadata=record.metadata)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def get_analysis(analysis_id: str) -> dict[str, Any]:
    """Return the cached summary for an analysis id."""

    try:
        record = await asyncio.to_thread(manager().load, analysis_id)
        return _ok(summary=record.summary.model_dump(), metadata=record.metadata)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def list_analyses() -> dict[str, Any]:
    """List cached analyses, newest first."""

    try:
        summaries = await asyncio.to_thread(manager().list_analyses)
        payload = AnalysisList(analyses=summaries, count=len(summaries))
        return _ok(**payload.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def delete_analysis(analysis_id: str) -> dict[str, Any]:
    """Delete a cached analysis and its artifacts."""

    try:
        await asyncio.to_thread(manager().delete, analysis_id)
        return _ok(analysis_id=analysis_id, deleted=True)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def cache_stats(include_entries: bool = True) -> dict[str, Any]:
    """Report cache size, age and per-analysis entries.

    Args:
        include_entries: Include the per-analysis breakdown (default true).
    """

    try:
        stats = await asyncio.to_thread(manager().cache_stats, include_entries=include_entries)
        return _ok(stats=stats.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def cleanup_cache(
    max_age_days: float | None = None,
    max_bytes: int | None = None,
    dry_run: bool = False,
    include_repos: bool = False,
) -> dict[str, Any]:
    """Prune old/large cached analyses.

    Args:
        max_age_days: Remove analyses older than this many days. Defaults to
            PYOPENAPI_CACHE_MAX_AGE_DAYS.
        max_bytes: Keep total analysis size under this many bytes, deleting the
            oldest first. Defaults to PYOPENAPI_CACHE_MAX_BYTES (0 disables).
        dry_run: Report what would be removed without deleting anything.
        include_repos: Also remove cached GitHub clones.
    """

    try:
        stats = await asyncio.to_thread(
            manager().cleanup_cache,
            max_age_days=max_age_days,
            max_bytes=max_bytes,
            dry_run=dry_run,
            include_repos=include_repos,
        )
        return _ok(stats=stats.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def extract_endpoints(
    analysis_id: str,
    method: str | None = None,
    path_prefix: str | None = None,
    view: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 500,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """Return endpoints from a cached analysis with optional filtering.

    Args:
        analysis_id: Id returned by analyze_project.
        method: Filter by HTTP method (GET, POST, ...).
        path_prefix: Only paths starting with this string.
        view: Substring match on the view/class name.
        search: Substring match on path, view or handler function.
        limit: Maximum endpoints to return (default 500).
        offset: Pagination offset.
    """

    try:
        record = await asyncio.to_thread(manager().load, analysis_id)
        filtered = filter_endpoints(
            record.endpoints, method=method, path_prefix=path_prefix, view=view, search=search
        )
        total = len(filtered)
        limit = max(1, min(limit, 5000))
        offset = max(0, offset)
        page = filtered[offset : offset + limit]
        endpoints: list[EndpointInfo] = [summarize_endpoint(item) for item in page]
        return _ok(
            analysis_id=analysis_id,
            total=total,
            count=len(endpoints),
            offset=offset,
            limit=limit,
            endpoints=[item.model_dump() for item in endpoints],
        )
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def extract_components(
    analysis_id: str,
    kind: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 500,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """Return schema components from a cached analysis.

    Args:
        analysis_id: Id returned by analyze_project.
        kind: Filter by component kind (serializer, pydantic_model, marshmallow_schema, enum).
        search: Substring match on component name or source file.
        limit: Maximum components to return (default 500).
        offset: Pagination offset.
    """

    try:
        record = await asyncio.to_thread(manager().load, analysis_id)
        items = list(record.components.items())
        if kind:
            needle = kind.lower()
            items = [pair for pair in items if str(pair[1].get("type", "")).lower() == needle]
        if search:
            needle = search.lower()
            items = [
                pair
                for pair in items
                if needle in pair[0].lower() or needle in str(pair[1].get("path", "")).lower()
            ]
        total = len(items)
        limit = max(1, min(limit, 5000))
        offset = max(0, offset)
        page = items[offset : offset + limit]
        components: list[ComponentInfo] = [summarize_component(name, raw) for name, raw in page]
        return _ok(
            analysis_id=analysis_id,
            total=total,
            count=len(components),
            offset=offset,
            limit=limit,
            components=[item.model_dump() for item in components],
        )
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def generate_openapi(
    analysis_id: str | None = None,
    project_path: str | None = None,
    output_dir: str | None = None,
    engine: EngineName = "auto",
    fmt: FormatName = "yml",
    spec_model: str | None = None,
    context_model: str | None = None,
    use_dynamic: bool = False,
    django_settings_module: str | None = None,
    django_settings_file: str | None = None,
    django_urls_file: str | None = None,
    batch_size: int | None = None,
    max_concurrency: int | None = None,
    skip_components: bool = False,
    skip_missing_context: bool = False,
    force_add_components: bool = False,
    openapi_version: OpenAPIVersion | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Generate an OpenAPI specification.

    Args:
        analysis_id: Use a cached analysis. If omitted, project_path is analyzed.
        project_path: Local project path or GitHub repository URL (used when
            analysis_id is omitted).
        output_dir: Where to write openapi.yml/json. Defaults to
            <project_root>/output/ for local projects, otherwise cache.
        engine: "auto" (configured provider, else static), "static", "nvidia",
            "lmstudio", "llamacpp", "ollama", "openai-compatible", or legacy "llm".
        fmt: "yml" (or "yaml") or "json".
        spec_model: LLM model for schema/request/response generation.
        context_model: LLM model for missing-context resolution.
        use_dynamic: Django only; use runtime URLconf extraction.
        django_settings_module: Django settings module for the LLM/dynamic pipeline.
        django_settings_file: Path to settings.py.
        django_urls_file: Path to the root urls.py.
        batch_size: LLM batch size (Django legacy pipeline).
        max_concurrency: LLM request concurrency.
        skip_components: Django LLM only; skip schema component generation.
        skip_missing_context: Django LLM only; skip missing-symbol resolution.
        force_add_components: Django LLM only; keep components that fail validation.
        openapi_version: "3.0.3" (default) or "3.1.0".
    """

    try:
        await _progress(ctx, 0, 3, "Resolving analysis")
        record = await _resolve_record(
            analysis_id,
            project_path,
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
        )
        # Default output to project's output/ directory for local projects
        if output_dir:
            out = Path(output_dir)
        elif project_path and Path(project_path).expanduser().exists():
            out = Path(project_path).expanduser().resolve() / "output"
        elif record.summary.project_path and Path(record.summary.project_path).expanduser().exists():
            out = Path(record.summary.project_path).expanduser().resolve() / "output"
        else:
            out = Path(record.summary.output_dir) / "output"
        settings = get_settings()
        await _progress(ctx, 1, 3, "Generating OpenAPI document")
        result = await _generate_openapi(
            record,
            out,
            engine=engine,
            fmt=fmt,
            spec_model=spec_model or settings.spec_model,
            context_model=context_model or settings.context_model,
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
            batch_size=batch_size or settings.batch_size,
            max_concurrency=max_concurrency or settings.max_concurrency,
            skip_components=skip_components,
            skip_missing_context=skip_missing_context,
            force_add_components=force_add_components,
            openapi_version=openapi_version,
            logger=logger,
        )
        await _progress(ctx, 3, 3, "OpenAPI generation complete")
        return _ok(
            result=result.model_dump(),
            llm_available=llm_available(),
            output_dir=str(out),
            spec_file=result.spec_path,
        )
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def generate_markdown(
    analysis_id: str | None = None,
    project_path: str | None = None,
    spec_path: str | None = None,
    openapi: str | None = None,
    output_dir: str | None = None,
    layout: LayoutName = "multi",
    title: str | None = None,
) -> dict[str, Any]:
    """Generate deterministic Markdown documentation from an OpenAPI document.

    Provide exactly one source: analysis_id (builds a static spec), project_path
    (analyzes and builds), spec_path, or openapi (raw YAML/JSON string).

    Args:
        analysis_id: Build a static OpenAPI spec from a cached analysis.
        project_path: Local project path to analyze and document (one-shot).
        spec_path: Path to an existing openapi.yml/yaml/json file.
        openapi: Raw OpenAPI YAML or JSON content.
        output_dir: Output directory. Defaults to <project_root>/output/markdown/
            for local projects, otherwise cache.
        layout: "multi" (index + endpoints/ + components/) or "single" (api.md).
        title: Override the API title.
    """

    try:
        sources = [s for s in (analysis_id, project_path, spec_path, openapi) if s is not None]
        if len(sources) != 1:
            return _fail(
                PyOpenAPIError(
                    "Provide exactly one of analysis_id, project_path, spec_path or openapi.",
                    hint="Pass a single source so the tool knows what to document.",
                )
            )

        project_root = None
        if analysis_id:
            record = await asyncio.to_thread(manager().load, analysis_id)
            spec: Any = build_openapi_from_analysis(record)
            project_root = Path(record.summary.project_path).expanduser().resolve() if Path(record.summary.project_path).expanduser().exists() else None
            default_out = Path(record.summary.output_dir) / "markdown"
        elif project_path:
            record = await asyncio.to_thread(
                manager().analyze,
                project_path,
                "auto",
                use_dynamic=False,
            )
            spec = build_openapi_from_analysis(record)
            project_root = Path(project_path).expanduser().resolve() if Path(project_path).expanduser().exists() else None
            default_out = Path(record.summary.output_dir) / "markdown"
        elif spec_path:
            spec = load_spec(spec_path)
            project_root = Path(spec_path).resolve().parent if Path(spec_path).resolve().parent.exists() else None
            default_out = Path(spec_path).resolve().parent / "markdown"
        else:
            spec = load_spec(str(openapi))
            default_out = get_settings().resolved_cache_dir() / "markdown"

        # Default to project's output/markdown/ for local projects
        if output_dir:
            out = Path(output_dir)
        elif project_root:
            out = project_root / "output" / "markdown"
        elif analysis_id and Path(record.summary.project_path).expanduser().exists():
            out = Path(record.summary.project_path).expanduser().resolve() / "output" / "markdown"
        else:
            out = default_out
        result: MarkdownResult = await asyncio.to_thread(
            _generate_markdown, spec, out, layout=layout, title=title
        )
        return _ok(
            result=result.model_dump(),
            output_dir=str(out),
            files=[f.model_dump() for f in result.files],
        )
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def validate_openapi(
    analysis_id: str | None = None,
    spec_path: str | None = None,
    openapi: str | None = None,
    openapi_version: OpenAPIVersion | None = None,
) -> dict[str, Any]:
    """Validate an OpenAPI document structurally.

    Provide exactly one source: analysis_id (builds a static spec), spec_path, or
    openapi (raw YAML/JSON string).
    """

    try:
        if sum(item is not None for item in (analysis_id, spec_path, openapi)) != 1:
            return _fail(
                PyOpenAPIError(
                    "Provide exactly one of analysis_id, spec_path or openapi.",
                    hint="Pass a single source so the tool knows what to validate.",
                )
            )
        if analysis_id:
            record = await asyncio.to_thread(manager().load, analysis_id)
            source: Any = build_openapi_from_analysis(
                record, openapi_version=openapi_version or get_settings().openapi_version
            )
        elif spec_path:
            source = spec_path
        else:
            source = str(openapi)

        report: ValidationReport = await asyncio.to_thread(validate_spec, source)
        return _ok(report=report.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def merge_specs(
    specs: list[str],
    title: str | None = None,
    version: str = "0.1.0",
    output_dir: str | None = None,
    fmt: FormatName = "yml",
    openapi_version: OpenAPIVersion | None = None,
) -> dict[str, Any]:
    """Merge multiple OpenAPI documents (paths or analysis ids) into one.

    Args:
        specs: OpenAPI file paths and/or cached analysis ids.
        title: Title for the merged document.
        version: Version for the merged document.
        output_dir: Where to write the merged spec. Defaults to <cache>/merged.
        fmt: "yml", "yaml" or "json".
        openapi_version: Override the output OpenAPI version.
    """

    try:
        documents = [await _load_source(item) for item in specs]
        merged = await asyncio.to_thread(
            _merge_specs,
            documents,
            title=title,
            version=version,
            openapi_version=openapi_version,
        )
        out = Path(output_dir) if output_dir else get_settings().resolved_cache_dir() / "merged"
        target = await asyncio.to_thread(write_spec, merged, out, fmt=fmt)
        paths = merged.get("paths") or {}
        schemas = (merged.get("components") or {}).get("schemas") or {}
        result = {
            "output_path": str(target),
            "spec_count": len(documents),
            "path_count": len(paths),
            "component_count": len(schemas),
            "warnings": [],
        }
        return _ok(result=result)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def diff_specs(old: str, new: str) -> dict[str, Any]:
    """Compare two OpenAPI documents (paths or analysis ids).

    Args:
        old: Baseline OpenAPI file path or cached analysis id.
        new: Updated OpenAPI file path or cached analysis id.
    """

    try:
        old_doc = await _load_source(old)
        new_doc = await _load_source(new)
        result = await asyncio.to_thread(_diff_specs, old_doc, new_doc)
        return _ok(result=result.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def analyze_workspace(
    workspace_path: str,
    framework: FrameworkName = "auto",
    use_dynamic: bool = False,
    merge: bool = True,
    output_dir: str | None = None,
    force: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Analyze every Python web project directly inside a workspace directory.

    Each immediate subdirectory containing project markers (manage.py, app.py,
    main.py, pyproject.toml, setup.py or requirements.txt) is analyzed. With
    ``merge=True`` the resulting static specs are combined into one document.

    Args:
        workspace_path: Directory containing multiple projects.
        framework: "auto" or a specific framework to force.
        use_dynamic: Django only; runtime URLconf extraction.
        merge: Merge generated specs into workspace-openapi.yml.
        output_dir: Where to write merged output. Defaults to <workspace>/.pyopenapi.
        force: Re-run analysis even when cached.
    """

    try:
        from ..detection import detect_framework as _detect

        root = Path(workspace_path).expanduser().resolve()
        if not root.is_dir():
            raise PyOpenAPIError(
                f"Workspace directory not found: {root}",
                hint="Pass a directory that contains one or more Python projects.",
            )

        markers = ("manage.py", "app.py", "main.py", "pyproject.toml", "setup.py", "requirements.txt")
        children = [
            child
            for child in sorted(root.iterdir())
            if child.is_dir() and not child.name.startswith(".") and any((child / m).is_file() for m in markers)
        ]
        if not children:
            raise PyOpenAPIError(
                f"No Python projects found directly under {root}.",
                hint=f"Expected subdirectories containing one of: {', '.join(markers)}.",
            )

        settings = get_settings()
        projects: list[WorkspaceProject] = []
        documents: list[dict[str, Any]] = []
        total = len(children) + (1 if merge else 0)
        step = 0

        for child in children:
            step += 1
            await _progress(ctx, step, total, f"Analyzing {child.name}")
            entry = WorkspaceProject(name=child.name, path=str(child))
            try:
                if framework == "auto":
                    detection = await asyncio.to_thread(
                        _detect,
                        child,
                        max_files=settings.max_scan_files,
                        max_file_bytes=settings.max_file_bytes,
                    )
                    detected = detection.framework
                    entry.framework = detected
                    if detected not in {"django", "flask", "fastapi"}:
                        entry.ok = False
                        entry.error = f"Unsupported or undetected framework: {detected}"
                        projects.append(entry)
                        continue
                    chosen = detected
                else:
                    chosen = framework
                    entry.framework = chosen

                record = await asyncio.to_thread(
                    manager().analyze,
                    str(child),
                    chosen,
                    use_dynamic=use_dynamic,
                    force=force,
                )
                entry.analysis_id = record.summary.analysis_id
                entry.endpoint_count = record.summary.endpoint_count
                entry.component_count = record.summary.component_count
                spec = await asyncio.to_thread(build_openapi_from_analysis, record)
                documents.append(spec)
            except Exception as exc:
                entry.ok = False
                entry.error = str(exc)
            projects.append(entry)

        merged_path: str | None = None
        warnings: list[str] = []
        if merge and documents:
            step += 1
            await _progress(ctx, step, total, "Merging specs")
            try:
                merged = await asyncio.to_thread(
                    _merge_specs,
                    documents,
                    title=f"{root.name} workspace API",
                )
                out = Path(output_dir) if output_dir else root / ".pyopenapi"
                target = await asyncio.to_thread(write_spec, merged, out, fmt="yml")
                merged_path = str(target)
                warnings.extend(
                    f"{child.name}: {child.error}" for child in projects if not child.ok
                )
            except Exception as exc:
                warnings.append(f"Merge failed: {exc}")

        result = WorkspaceResult(
            workspace_path=str(root),
            project_count=len(projects),
            ok_count=sum(1 for item in projects if item.ok),
            failed_count=sum(1 for item in projects if not item.ok),
            projects=projects,
            merged_spec_path=merged_path,
            warnings=warnings,
        )
        return _ok(result=result.model_dump())
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def generate_docs(
    project_path: str,
    framework: FrameworkName = "auto",
    output_dir: str | None = None,
    engine: EngineName = "auto",
    layout: LayoutName = "multi",
    use_dynamic: bool = False,
    django_settings_module: str | None = None,
    django_settings_file: str | None = None,
    django_urls_file: str | None = None,
    openapi_version: OpenAPIVersion | None = None,
    force: bool = False,
    refresh_repo: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """One-shot workflow: analyze, generate OpenAPI, then generate Markdown.

    Args:
        project_path: Local project path or GitHub repository URL.
        framework: "auto", "django", "flask" or "fastapi".
        output_dir: Base output directory. Defaults to <cache>/analyses/<id>/output.
        engine: "auto", "static", "nvidia", "lmstudio", "llamacpp", "ollama",
            "openai-compatible", or legacy "llm".
        layout: Markdown layout ("multi" or "single").
        use_dynamic: Django only; runtime URLconf extraction.
        django_settings_module: Django settings module.
        django_settings_file: Path to settings.py.
        django_urls_file: Path to the root urls.py.
        openapi_version: "3.0.3" (default) or "3.1.0".
        force: Re-run analysis even if cached.
        refresh_repo: Re-clone a GitHub repository URL before analysis.
    """

    try:
        await _progress(ctx, 0, 4, "Analyzing project")
        record = await asyncio.to_thread(
            manager().analyze,
            project_path,
            framework,
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
            force=force,
            refresh_repo=refresh_repo,
        )
        settings = get_settings()
        out = Path(output_dir) if output_dir else Path(record.summary.output_dir) / "output"
        await _progress(ctx, 1, 4, "Generating OpenAPI document")
        generation = await _generate_openapi(
            record,
            out,
            engine=engine,
            spec_model=settings.spec_model,
            context_model=settings.context_model,
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
            batch_size=settings.batch_size,
            max_concurrency=settings.max_concurrency,
            openapi_version=openapi_version,
            logger=logger,
        )
        await _progress(ctx, 2, 4, "Generating Markdown")
        spec = load_spec(generation.spec_path)
        markdown = await asyncio.to_thread(
            _generate_markdown, spec, Path(out) / "markdown", layout=layout
        )
        await _progress(ctx, 4, 4, "Documentation complete")
        return _ok(
            analysis=record.summary.model_dump(),
            openapi=generation.model_dump(),
            markdown=markdown.model_dump(),
        )
    except Exception as exc:
        return _fail(exc)


async def _load_source(source: str | Path | dict[str, Any]) -> Any:
    """Resolve an analysis id, file path or mapping to an OpenAPI document."""

    import re

    if isinstance(source, dict):
        return source
    source = str(source)
    candidate = Path(source).expanduser()
    if candidate.is_file():
        return await asyncio.to_thread(load_spec, candidate)
    if re.fullmatch(r"[0-9a-f]{8,64}", source):
        record = await asyncio.to_thread(manager().load, source)
        return await asyncio.to_thread(build_openapi_from_analysis, record)
    if candidate.exists():
        raise PyOpenAPIError(
            f"Not an OpenAPI file: {candidate}",
            hint="Pass a .json/.yml/.yaml file or a cached analysis id.",
        )
    raise PyOpenAPIError(
        f"Could not resolve '{source}' to an OpenAPI file or analysis id.",
        hint="Call list_analyses to see cached ids, or pass a spec file path.",
    )


async def _resolve_record(
    analysis_id: str | None,
    project_path: str | None,
    *,
    use_dynamic: bool = False,
    django_settings_module: str | None = None,
    django_settings_file: str | None = None,
    django_urls_file: str | None = None,
):
    if analysis_id:
        return await asyncio.to_thread(manager().load, analysis_id)
    if project_path:
        return await asyncio.to_thread(
            manager().analyze,
            project_path,
            "auto",
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
        )
    raise PyOpenAPIError(
        "Provide analysis_id or project_path.",
        hint="Run analyze_project first, or pass project_path for a one-shot analysis.",
    )
