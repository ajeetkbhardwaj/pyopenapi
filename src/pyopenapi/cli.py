"""Command line interface for pyopenapi.

The default action (no subcommand, or only flags) is to run the MCP server over
stdio, which makes ``uvx pyopenapi`` and editor integrations work out of the box.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _serve(args: argparse.Namespace) -> int:
    if args.transport:
        os.environ["PYOPENAPI_TRANSPORT"] = args.transport
    if args.host:
        os.environ["PYOPENAPI_HOST"] = args.host
    if args.port:
        os.environ["PYOPENAPI_PORT"] = str(args.port)
    if args.log_level:
        os.environ["PYOPENAPI_LOG_LEVEL"] = args.log_level

    from .server.app import run

    run()
    return 0


def _detect(args: argparse.Namespace) -> int:
    from .detection import detect_framework
    from .source import resolve_project_source

    _print(detect_framework(resolve_project_source(args.project)).model_dump())
    return 0


def _analyze(args: argparse.Namespace) -> int:
    from .core.analysis import AnalysisManager

    manager = AnalysisManager()
    record = manager.analyze(
        args.project,
        args.framework,
        use_dynamic=args.dynamic,
        django_settings_module=args.settings_module,
        django_settings_file=args.settings_file,
        django_urls_file=args.urls_file,
        force=args.force,
        refresh_repo=args.refresh_repo,
    )
    _print(record.summary.model_dump())
    return 0


def _list(_: argparse.Namespace) -> int:
    from .core.analysis import AnalysisManager

    summaries = AnalysisManager().list_analyses()
    _print({"count": len(summaries), "analyses": [item.model_dump() for item in summaries]})
    return 0


def _generate(args: argparse.Namespace) -> int:
    from .core.analysis import AnalysisManager
    from .core.spec import generate_openapi_sync

    manager = AnalysisManager()
    record = manager.analyze(
        args.project,
        args.framework,
        use_dynamic=args.dynamic,
        django_settings_module=args.settings_module,
        django_settings_file=args.settings_file,
        django_urls_file=args.urls_file,
        force=args.force,
        refresh_repo=args.refresh_repo,
    )
    output = Path(args.output) if args.output else Path(record.summary.output_dir) / "output"
    dashboard = None
    if getattr(args, "dashboard", False):
        from .core.dashboard import DashboardServer

        dashboard = DashboardServer(
            output,
            host=args.dashboard_host,
            port=args.dashboard_port,
            open_browser=args.dashboard_open,
        )
        url = dashboard.start()
        print(f"pyopenapi dashboard: {url}", file=sys.stderr, flush=True)
    result = generate_openapi_sync(
        record,
        output,
        engine=args.engine,
        fmt=args.format,
        use_dynamic=args.dynamic,
        openapi_version=args.openapi_version,
        spec_model=getattr(args, "spec_model", None),
        max_concurrency=getattr(args, "max_concurrency", None),
        allow_partial=getattr(args, "allow_partial", False),
        progress_path=output / "agent_progress.jsonl",
    )
    _print(result.model_dump())
    if dashboard is not None:
        print("Dashboard live; press Ctrl-C to stop.", file=sys.stderr, flush=True)
        dashboard.wait()
    return 0


def _dashboard(args: argparse.Namespace) -> int:
    from .core.dashboard import serve_dashboard

    serve_dashboard(
        args.output,
        host=args.host,
        port=args.port,
        open_browser=args.open,
    )
    return 0


def _cache(args: argparse.Namespace) -> int:
    from .core.analysis import AnalysisManager

    manager = AnalysisManager()
    if args.cleanup:
        stats = manager.cleanup_cache(
            max_age_days=args.max_age_days,
            max_bytes=args.max_bytes,
            dry_run=args.dry_run,
            include_repos=args.include_repos,
        )
    else:
        stats = manager.cache_stats()
    _print(stats.model_dump())
    return 0


def _merge(args: argparse.Namespace) -> int:
    from .core.spec import merge_specs, write_spec

    merged = merge_specs(
        args.specs,
        title=args.title,
        version=args.version,
        openapi_version=args.openapi_version,
    )
    output = Path(args.output) if args.output else Path(".pyopenapi") / "merged"
    target = write_spec(merged, output, fmt=args.format)
    paths = merged.get("paths") or {}
    schemas = (merged.get("components") or {}).get("schemas") or {}
    _print(
        {
            "output_path": str(target),
            "spec_count": len(args.specs),
            "path_count": len(paths),
            "component_count": len(schemas),
        }
    )
    return 0


def _diff(args: argparse.Namespace) -> int:
    from .core.spec import diff_specs

    result = diff_specs(args.old, args.new)
    _print(result.model_dump())
    return 0 if not result.breaking else 1


def _workspace(args: argparse.Namespace) -> int:
    from .server.tools import analyze_workspace

    result = asyncio.run(
        analyze_workspace(
            args.workspace,
            framework=args.framework,
            use_dynamic=args.dynamic,
            merge=not args.no_merge,
            output_dir=args.output,
            force=args.force,
        )
    )
    _print(result)
    return 0 if result.get("ok") else 1


def _resolve_source(source: str) -> tuple[str | None, str | None]:
    path = Path(source).expanduser()
    if path.is_file():
        return (None, str(path.resolve()))
    return (source, None)


def _markdown(args: argparse.Namespace) -> int:
    from .core.analysis import AnalysisManager
    from .core.markdown import generate_markdown
    from .core.spec import build_openapi_from_analysis

    analysis_id, spec_path = _resolve_source(args.source)
    if analysis_id:
        record = AnalysisManager().load(analysis_id)
        spec = build_openapi_from_analysis(record)
        default_out = Path(record.summary.output_dir) / "markdown"
    else:
        spec = spec_path
        default_out = Path(spec_path).parent / "markdown"

    output = Path(args.output) if args.output else default_out
    result = generate_markdown(spec, output, layout=args.layout, title=args.title)
    _print(result.model_dump())
    return 0


def _validate(args: argparse.Namespace) -> int:
    from .core.analysis import AnalysisManager
    from .core.spec import build_openapi_from_analysis
    from .core.validation import validate_spec

    analysis_id, spec_path = _resolve_source(args.source)
    source: Any = spec_path
    if analysis_id:
        record = AnalysisManager().load(analysis_id)
        source = build_openapi_from_analysis(record)

    report = validate_spec(source, strict=getattr(args, "strict", False))
    _print(report.model_dump())
    return 0 if report.valid else 1



def _traces(args: argparse.Namespace) -> int:
    import asyncio

    from .server import tools
    result = asyncio.run(tools.get_generation_trace(args.source))
    _print(result)
    return 0 if result.get("ok") else 1


def _quality(args: argparse.Namespace) -> int:
    import asyncio

    from .server import tools
    result = asyncio.run(tools.get_quality_report(args.source))
    _print(result)
    return 0 if result.get("ok") else 1


def _progress(args: argparse.Namespace) -> int:
    import asyncio

    from .server import tools
    result = asyncio.run(tools.get_generation_progress(args.source))
    _print(result)
    return 0 if result.get("ok") else 1

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyopenapi",
        description="MCP server and CLI for source-code driven OpenAPI generation.",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--transport", choices=["stdio", "sse", "streamable-http"])
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    serve.set_defaults(func=_serve)

    detect = sub.add_parser("detect", help="Detect the web framework")
    detect.add_argument("project", help="Local project path or GitHub repository URL")
    detect.set_defaults(func=_detect)

    analyze = sub.add_parser("analyze", help="Analyze a project and cache results")
    analyze.add_argument("project", help="Local project path or GitHub repository URL")
    analyze.add_argument("--framework", default="auto")
    analyze.add_argument("--dynamic", action="store_true", help="Django runtime URLconf extraction")
    analyze.add_argument("--settings-module")
    analyze.add_argument("--settings-file")
    analyze.add_argument("--urls-file")
    analyze.add_argument("--force", action="store_true")
    analyze.add_argument("--refresh-repo", action="store_true", help="Re-clone a GitHub URL")
    analyze.set_defaults(func=_analyze)

    listing = sub.add_parser("list", help="List cached analyses")
    listing.set_defaults(func=_list)

    generate = sub.add_parser("generate", help="Analyze and generate an OpenAPI spec")
    generate.add_argument("project", help="Local project path or GitHub repository URL")
    generate.add_argument("--framework", default="auto")
    generate.add_argument(
        "--engine",
        default="auto",
        choices=["auto", "agent", "static", "nvidia", "openrouter", "lmstudio", "llamacpp", "ollama", "openai-compatible", "llm"],
    )
    generate.add_argument("--format", default="yml", choices=["yml", "yaml", "json"])
    generate.add_argument("--output")
    generate.add_argument("--dynamic", action="store_true")
    generate.add_argument("--settings-module")
    generate.add_argument("--settings-file")
    generate.add_argument("--urls-file")
    generate.add_argument("--openapi-version", choices=["3.0.3", "3.1.0"])
    generate.add_argument("--force", action="store_true")
    generate.add_argument("--refresh-repo", action="store_true", help="Re-clone a GitHub URL")
    generate.add_argument("--dashboard", action="store_true", help="Serve a live progress dashboard")
    generate.add_argument("--dashboard-host", default="127.0.0.1")
    generate.add_argument("--dashboard-port", type=int, default=8765)
    generate.add_argument("--dashboard-open", action="store_true", help="Open the dashboard in a browser")
    generate.add_argument("--spec-model", help="LLM model for agent/spec generation")
    generate.add_argument("--max-concurrency", type=int, help="Max parallel agent reasoning tasks")
    generate.add_argument(
        "--allow-partial",
        action="store_true",
        help="Emit a partial spec when some agent items fail",
    )
    generate.set_defaults(func=_generate)

    dashboard = sub.add_parser("dashboard", help="Serve a live dashboard for an output directory")
    dashboard.add_argument("output", help="Output directory containing agent_progress.jsonl")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--open", action="store_true", help="Open the dashboard in a browser")
    dashboard.set_defaults(func=_dashboard)

    traces = sub.add_parser("traces", help="Show agent generation trace for an output/analysis")
    traces.add_argument("source", help="Analysis id, output dir, or agent_trace.json path")
    traces.set_defaults(func=_traces)

    quality = sub.add_parser("quality", help="Show agent quality report for an output/analysis")
    quality.add_argument("source", help="Analysis id, output dir, or agent_trace.json path")
    quality.set_defaults(func=_quality)

    progress_cmd = sub.add_parser("progress", help="Show agent progress state for an output/analysis")
    progress_cmd.add_argument("source", help="Analysis id, output dir, or agent_progress.jsonl path")
    progress_cmd.set_defaults(func=_progress)

    cache = sub.add_parser("cache", help="Show or prune the analysis cache")
    cache.add_argument("--cleanup", action="store_true", help="Prune old/large analyses")
    cache.add_argument("--max-age-days", type=float)
    cache.add_argument("--max-bytes", type=int)
    cache.add_argument("--dry-run", action="store_true")
    cache.add_argument("--include-repos", action="store_true", help="Also remove cached GitHub clones")
    cache.set_defaults(func=_cache)

    merge = sub.add_parser("merge", help="Merge multiple OpenAPI documents")
    merge.add_argument("specs", nargs="+", help="OpenAPI file paths")
    merge.add_argument("--title")
    merge.add_argument("--version", default="0.1.0")
    merge.add_argument("--output")
    merge.add_argument("--format", default="yml", choices=["yml", "yaml", "json"])
    merge.add_argument("--openapi-version", choices=["3.0.3", "3.1.0"])
    merge.set_defaults(func=_merge)

    diff = sub.add_parser("diff", help="Compare two OpenAPI documents")
    diff.add_argument("old")
    diff.add_argument("new")
    diff.set_defaults(func=_diff)

    workspace = sub.add_parser("workspace", help="Analyze every project in a workspace directory")
    workspace.add_argument("workspace")
    workspace.add_argument("--framework", default="auto", choices=["auto", "django", "flask", "fastapi"])
    workspace.add_argument("--dynamic", action="store_true")
    workspace.add_argument("--no-merge", action="store_true")
    workspace.add_argument("--output")
    workspace.add_argument("--force", action="store_true")
    workspace.set_defaults(func=_workspace)

    markdown = sub.add_parser("markdown", help="Generate Markdown from an analysis id or spec file")
    markdown.add_argument("source")
    markdown.add_argument("--output")
    markdown.add_argument("--layout", default="multi", choices=["multi", "single"])
    markdown.add_argument("--title")
    markdown.set_defaults(func=_markdown)

    validate = sub.add_parser("validate", help="Validate an analysis id or OpenAPI file")
    validate.add_argument("source")
    validate.add_argument("--strict", action="store_true", help="Run full OpenAPI schema validation")
    validate.set_defaults(func=_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Default to serving over stdio when invoked with no subcommand or serve flags.
    if not argv:
        argv = ["serve"]
    elif argv[0].startswith("-") and argv[0] not in {"-h", "--help"}:
        argv = ["serve", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
