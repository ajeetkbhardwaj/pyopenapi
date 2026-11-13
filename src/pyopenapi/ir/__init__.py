"""Framework-agnostic intermediate representation (IR) for pyopenapi."""

from __future__ import annotations

from .models import (
    AuthHint,
    Confidence,
    ContextHint,
    NormalizedComponent,
    NormalizedEndpoint,
    NormalizedField,
    NormalizedOperation,
    NormalizedParameter,
    NormalizedProject,
    NormalizedRequest,
    NormalizedResponse,
    SourceRef,
    stable_id,
)
from .normalizer import ProjectNormalizer, normalize_endpoints, normalize_project
from .registry import get_normalizer, normalize_analysis

__all__ = [
    "AuthHint",
    "Confidence",
    "ContextHint",
    "NormalizedComponent",
    "NormalizedEndpoint",
    "NormalizedField",
    "NormalizedOperation",
    "NormalizedParameter",
    "NormalizedProject",
    "NormalizedRequest",
    "NormalizedResponse",
    "ProjectNormalizer",
    "SourceRef",
    "get_normalizer",
    "normalize_analysis",
    "normalize_endpoints",
    "normalize_project",
    "stable_id",
]
