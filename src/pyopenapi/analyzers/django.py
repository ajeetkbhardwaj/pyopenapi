"""Django analyzer adapter.

Uses the upstream ``PythonCodeAnalyzer`` + ``DjangoAnalyzer`` for rich component
extraction, and falls back to the dependency-free static URL extractor when the
dynamic (runtime) extraction is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bootstrap import ensure_knowl_on_path
from ..errors import AnalysisError
from ..utils import redirect_stdout_to_stderr
from .base import AnalysisOptions, RawAnalysis
from .django_components_static import enrich_django_components
from .django_static import extract_django_endpoints


def analyze_django(
    project_path: Path,
    output_dir: Path,
    options: AnalysisOptions,
    logger: Any,
) -> RawAnalysis:
    ensure_knowl_on_path()

    try:
        from python_analyzer import PythonCodeAnalyzer
        from django_analyzer import DjangoAnalyzer
    except Exception as exc:  # pragma: no cover - broken installation
        raise AnalysisError(
            "Could not import the Django analyzer from knowl-apidocs.",
            hint="Ensure PYOPENAPI_KNOWL_ROOT points at a complete knowl-apidocs checkout.",
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    code_analyzer = PythonCodeAnalyzer()
    try:
        with redirect_stdout_to_stderr():
            analysis_file = code_analyzer.analyze_project(str(project_path), str(output_dir))
    except Exception as exc:
        raise AnalysisError(f"Python AST analysis failed for {project_path}: {exc}") from exc

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
