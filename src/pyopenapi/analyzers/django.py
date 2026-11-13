"""Django analyzer adapter.

Static (dependency-free) URL extraction is the default. The upstream
``PythonCodeAnalyzer`` + ``DjangoAnalyzer`` from knowl-apidocs are used only when
dynamic analysis is requested or ``PYOPENAPI_KNOWL_ROOT`` is configured.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..bootstrap import ensure_knowl_on_path
from ..utils import redirect_stdout_to_stderr
from .base import AnalysisOptions, RawAnalysis
from .django_components_static import discover_serializer_components, enrich_django_components
from .django_static import extract_django_endpoints


def _legacy_requested(options: AnalysisOptions) -> bool:
    return bool(
        options.use_dynamic
        or os.getenv("PYOPENAPI_KNOWL_ROOT")
        or os.getenv("KNOWL_APIDOCS_ROOT")
    )


def _load_legacy_analyzers() -> tuple[Any, Any] | None:
    """Return ``(PythonCodeAnalyzer, DjangoAnalyzer)`` classes when available."""

    try:
        ensure_knowl_on_path()
        from django_analyzer import DjangoAnalyzer
        from python_analyzer import PythonCodeAnalyzer
    except Exception:
        return None
    return PythonCodeAnalyzer, DjangoAnalyzer


def _static_analysis(
    project_path: Path,
    options: AnalysisOptions,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    endpoints: list[dict[str, Any]] = []
    components: dict[str, dict[str, Any]] = {}
    endpoint_source = "none"
    component_source = "none"

    if options.include_endpoints:
        endpoints, static_warnings = extract_django_endpoints(
            project_path,
            urls_file=options.django_urls_file,
            settings_file=options.django_settings_file,
            max_files=options.max_files,
            max_bytes=options.max_file_bytes,
        )
        warnings.extend(static_warnings)
        endpoint_source = "static" if endpoints else "none"

    if options.include_components:
        components, discovery_warnings = discover_serializer_components(
            project_path,
            max_files=options.max_files,
            max_bytes=options.max_file_bytes,
        )
        warnings.extend(discovery_warnings)
        if components:
            try:
                components, enrichment_warnings = enrich_django_components(
                    project_path,
                    components,
                    max_files=options.max_files,
                    max_bytes=options.max_file_bytes,
                )
                warnings.extend(enrichment_warnings)
                component_source = "static"
            except Exception as exc:
                warnings.append(f"Static component enrichment failed: {exc}")

    return endpoints, components, f"{endpoint_source}|{component_source}"


def analyze_django(
    project_path: Path,
    output_dir: Path,
    options: AnalysisOptions,
    logger: Any,
) -> RawAnalysis:
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    legacy = _load_legacy_analyzers() if _legacy_requested(options) else None
    if legacy is None:
        if options.use_dynamic:
            warnings.append(
                "Dynamic Django analysis was requested but the knowl-apidocs analyzers "
                "are unavailable; using static extraction."
            )
        endpoints, components, sources = _static_analysis(project_path, options, warnings)
        endpoint_source, component_source = sources.split("|", 1)
        metadata = {
            "analysis_file": None,
            "endpoint_source": endpoint_source,
            "component_source": component_source,
            "dynamic_requested": bool(options.use_dynamic),
            "dynamic_used": False,
            "degraded": bool(options.use_dynamic),
            "default_settings": {},
            "code_analyzer": "static",
        }
        return RawAnalysis(
            framework="django",
            endpoints=endpoints,
            components=components,
            warnings=warnings,
            metadata=metadata,
        )

    PythonCodeAnalyzer, DjangoAnalyzer = legacy
    code_analyzer = PythonCodeAnalyzer()
    try:
        with redirect_stdout_to_stderr():
            analysis_file = code_analyzer.analyze_project(str(project_path), str(output_dir))
    except Exception as exc:
        warnings.append(f"Legacy Python AST analysis failed: {exc}; using static extraction.")
        endpoints, components, sources = _static_analysis(project_path, options, warnings)
        endpoint_source, component_source = sources.split("|", 1)
        return RawAnalysis(
            framework="django",
            endpoints=endpoints,
            components=components,
            warnings=warnings,
            metadata={
                "analysis_file": None,
                "endpoint_source": endpoint_source,
                "component_source": component_source,
                "dynamic_requested": bool(options.use_dynamic),
                "dynamic_used": False,
                "degraded": True,
                "default_settings": {},
                "code_analyzer": "static",
            },
        )

    analyzer = DjangoAnalyzer(
        code_analyzer=code_analyzer,
        project_path=str(project_path),
        logger=logger,
        use_dynamic=options.use_dynamic,
        settings_module_str=options.django_settings_module,
        explicit_settings_file_path=options.django_settings_file,
        explicit_urls_file_path=options.django_urls_file,
    )

    endpoints: list[dict[str, Any]] = []
    endpoint_source = "none"
    dynamic_requested = bool(options.use_dynamic)
    dynamic_used = False
    degraded = False

    if options.include_endpoints:
        if options.use_dynamic:
            try:
                with redirect_stdout_to_stderr():
                    endpoints = analyzer.get_endpoints(output_dir=str(output_dir)) or []
            except Exception as exc:
                warnings.append(f"Dynamic endpoint extraction failed: {exc}")
                endpoints = []
            if endpoints:
                endpoint_source = "dynamic"
                dynamic_used = True
            else:
                degraded = True
                warnings.append(
                    "Dynamic endpoint extraction returned no endpoints; falling back to static URL analysis."
                )

        if not endpoints:
            static_endpoints, static_warnings = extract_django_endpoints(
                project_path,
                urls_file=options.django_urls_file,
                settings_file=options.django_settings_file,
                max_files=options.max_files,
                max_bytes=options.max_file_bytes,
            )
            endpoints = static_endpoints
            warnings.extend(static_warnings)
            endpoint_source = "static" if endpoints else "none"

    components: dict[str, dict[str, Any]] = {}
    component_source = "none"
    if options.include_components:
        try:
            with redirect_stdout_to_stderr():
                components = analyzer.get_schema_components() or {}
            component_source = "ast"
        except Exception as exc:
            warnings.append(f"Component extraction failed: {exc}")
            components = {}

        if components:
            try:
                components, enrichment_warnings = enrich_django_components(
                    project_path,
                    components,
                    max_files=options.max_files,
                    max_bytes=options.max_file_bytes,
                )
                warnings.extend(enrichment_warnings)
            except Exception as exc:
                warnings.append(f"Static component enrichment failed: {exc}")

    metadata = {
        "analysis_file": str(analysis_file),
        "endpoint_source": endpoint_source,
        "component_source": component_source,
        "dynamic_requested": dynamic_requested,
        "dynamic_used": dynamic_used,
        "degraded": degraded,
        "default_settings": getattr(analyzer, "default_settings", {}),
        "code_analyzer": type(code_analyzer).__name__,
    }

    return RawAnalysis(
        framework="django",
        endpoints=endpoints,
        components=components,
        warnings=warnings,
        metadata=metadata,
    )
