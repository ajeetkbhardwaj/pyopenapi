"""IR normalizer registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import FrameworkNotSupportedError
from .django import DjangoNormalizer
from .fastapi import FastAPINormalizer
from .flask import FlaskNormalizer
from .models import NormalizedProject
from .normalizer import ProjectNormalizer, normalize_project

_NORMALIZERS: dict[str, ProjectNormalizer] = {
    "django": DjangoNormalizer(),
    "flask": FlaskNormalizer(),
    "fastapi": FastAPINormalizer(),
}


def get_normalizer(framework: str) -> ProjectNormalizer:
    try:
        return _NORMALIZERS[framework.lower()]
    except KeyError as exc:
        raise FrameworkNotSupportedError(
            f"No IR normalizer registered for framework: {framework}",
            hint="Supported frameworks are: django, flask, fastapi.",
        ) from exc


def normalize_analysis(
    record: Any,
    *,
    framework: str | None = None,
    project_path: str | Path | None = None,
    title: str | None = None,
) -> NormalizedProject:
    summary = getattr(record, "summary", None)
    resolved = framework or getattr(summary, "framework", "unknown")
    normalizer = get_normalizer(str(resolved))
    return normalize_project(
        record,
        framework=normalizer.framework,
        project_path=project_path,
        title=title,
    )
