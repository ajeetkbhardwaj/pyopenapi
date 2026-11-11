"""Shared analyzer adapter types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class AnalysisOptions:
    framework: str = "auto"
    use_dynamic: bool = False
    django_settings_module: str | None = None
    django_settings_file: str | None = None
    django_urls_file: str | None = None
    include_endpoints: bool = True
    include_components: bool = True
    max_files: int = 20_000
    max_file_bytes: int = 2_000_000


@dataclass
class RawAnalysis:
    framework: str
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class AnalyzerAdapter(Protocol):
    name: str

    def analyze(
        self,
        project_path: Path,
        output_dir: Path,
        options: AnalysisOptions,
        logger: Any,
    ) -> RawAnalysis:  # pragma: no cover - protocol
        ...
