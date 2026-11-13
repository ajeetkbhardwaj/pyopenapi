"""Framework analyzer registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import FrameworkNotSupportedError
from .base import AnalysisOptions, RawAnalysis

__all__ = ["AnalysisOptions", "AnalyzerAdapter", "RawAnalysis", "analyze", "get_adapter"]


class AnalyzerAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    def analyze(
        self,
        project_path: Path,
        output_dir: Path,
        options: AnalysisOptions,
        logger: Any,
    ) -> RawAnalysis:  # pragma: no cover - overridden
        raise NotImplementedError


class _DjangoAdapter(AnalyzerAdapter):
    def __init__(self) -> None:
        super().__init__("django")

    def analyze(self, project_path, output_dir, options, logger):
        from .django import analyze_django

        return analyze_django(project_path, output_dir, options, logger)


class _FlaskAdapter(AnalyzerAdapter):
    def __init__(self) -> None:
        super().__init__("flask")

    def analyze(self, project_path, output_dir, options, logger):
        from .flask_static import analyze_flask

        raw = analyze_flask(
            project_path,
            logger,
            max_files=options.max_files,
            max_bytes=options.max_file_bytes,
        )
        if not options.include_endpoints:
            raw.endpoints = []
        if not options.include_components:
            raw.components = {}
        return raw


class _FastAPIAdapter(AnalyzerAdapter):
    def __init__(self) -> None:
        super().__init__("fastapi")

    def analyze(self, project_path, output_dir, options, logger):
        from .fastapi_static import analyze_fastapi

        raw = analyze_fastapi(
            project_path,
            logger,
            max_files=options.max_files,
            max_bytes=options.max_file_bytes,
        )
        if not options.include_endpoints:
            raw.endpoints = []
        if not options.include_components:
            raw.components = {}
        return raw


_ADAPTERS: dict[str, AnalyzerAdapter] = {
    "django": _DjangoAdapter(),
    "flask": _FlaskAdapter(),
    "fastapi": _FastAPIAdapter(),
}


def get_adapter(framework: str) -> AnalyzerAdapter:
    try:
        return _ADAPTERS[framework.lower()]
    except KeyError as exc:
        raise FrameworkNotSupportedError(
            f"Unsupported framework: {framework}",
            hint="Supported frameworks are: django, flask, fastapi.",
        ) from exc


def analyze(
    project_path: Path,
    output_dir: Path,
    options: AnalysisOptions,
    logger: Any,
) -> RawAnalysis:
    adapter = get_adapter(options.framework)
    return adapter.analyze(project_path, output_dir, options, logger)
