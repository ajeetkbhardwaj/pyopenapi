"""Heuristic web-framework detection for Python projects.

Detection is intentionally dependency-free: it inspects file names and source
text with a weighted signal model. The result includes the evidence used so
agents (and humans) can sanity check the decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import DetectionResult
from .utils import resolve_project_path

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    "dist",
    "build",
    ".eggs",
    ".idea",
    ".vscode",
    "migrations",
}

_SUPPORTED = {"django": True, "flask": True, "fastapi": True}
_PREVIEW = {"flask", "fastapi"}


@dataclass(frozen=True)
class _Signal:
    pattern: re.Pattern[str]
    weight: float
    label: str


def _sig(pattern: str, weight: float, label: str, flags: int = re.MULTILINE) -> _Signal:
    return _Signal(re.compile(pattern, flags), weight, label)


_DJANGO_SIGNALS: tuple[_Signal, ...] = (
    _sig(r"\bDJANGO_SETTINGS_MODULE\b", 4.0, "DJANGO_SETTINGS_MODULE"),
    _sig(r"^\s*from\s+django(\.|\s+import)", 3.0, "django import"),
    _sig(r"^\s*import\s+django\b", 3.0, "django import"),
    _sig(r"\bINSTALLED_APPS\b", 2.0, "INSTALLED_APPS"),
    _sig(r"\bROOT_URLCONF\b", 2.0, "ROOT_URLCONF"),
    _sig(r"\burlpatterns\s*=", 2.5, "urlpatterns"),
    _sig(r"^\s*from\s+rest_framework(\.|\s+import)", 4.0, "django-rest-framework import"),
    _sig(r"\bModelSerializer\b", 3.0, "DRF ModelSerializer"),
    _sig(r"\bSerializer\)", 1.5, "DRF Serializer"),
    _sig(r"\bViewSet\b", 2.5, "DRF ViewSet"),
    _sig(r"\bAPIView\b", 2.0, "DRF APIView"),
    _sig(r"@api_view\b", 2.0, "DRF @api_view"),
)

_FLASK_SIGNALS: tuple[_Signal, ...] = (
    _sig(r"^\s*from\s+flask(\.|\s+import)", 5.0, "flask import"),
    _sig(r"^\s*import\s+flask\b", 4.0, "flask import"),
    _sig(r"\bFlask\s*\(\s*__name__", 5.0, "Flask(__name__)"),
    _sig(r"\bBlueprint\s*\(", 3.5, "Blueprint"),
    _sig(r"@\w+\.route\s*\(", 3.0, "route decorator"),
    _sig(r"@\w+\.(get|post|put|patch|delete)\s*\(", 1.5, "method route decorator"),
    _sig(r"\bflask_restful\b", 3.5, "flask-restful"),
    _sig(r"\bflask_smorest\b", 3.5, "flask-smorest"),
    _sig(r"\bMethodView\b", 2.5, "MethodView"),
    _sig(r"\bmarshmallow\b", 1.5, "marshmallow"),
)

_FASTAPI_SIGNALS: tuple[_Signal, ...] = (
    _sig(r"^\s*from\s+fastapi(\.|\s+import)", 6.0, "fastapi import"),
    _sig(r"^\s*import\s+fastapi\b", 5.0, "fastapi import"),
    _sig(r"\bFastAPI\s*\(", 6.0, "FastAPI()"),
    _sig(r"\bAPIRouter\s*\(", 3.5, "APIRouter"),
    _sig(r"@\w+\.(get|post|put|patch|delete)\s*\(", 2.5, "route decorator"),
    _sig(r"^\s*from\s+pydantic(\.|\s+import)", 2.0, "pydantic import"),
    _sig(r"\bBaseModel\b", 1.5, "pydantic BaseModel"),
    _sig(r"\bDepends\s*\(", 2.0, "Depends()"),
    _sig(r"\bresponse_model\s*=", 2.0, "response_model"),
)

_SIGNALS: dict[str, tuple[_Signal, ...]] = {
    "django": _DJANGO_SIGNALS,
    "flask": _FLASK_SIGNALS,
    "fastapi": _FASTAPI_SIGNALS,
}

_IMPORT_MARKERS: dict[str, re.Pattern[str]] = {
    "django": re.compile(r"^\s*(from\s+django|import\s+django)\b", re.MULTILINE),
    "flask": re.compile(r"^\s*(from\s+flask|import\s+flask)\b", re.MULTILINE),
    "fastapi": re.compile(r"^\s*(from\s+fastapi|import\s+fastapi)\b", re.MULTILINE),
}

_MAX_EVIDENCE_PER_FRAMEWORK = 6


@dataclass
class _Accumulator:
    scores: dict[str, float] = field(
        default_factory=lambda: {name: 0.0 for name in _SIGNALS}
    )
    evidence: dict[str, list[str]] = field(
        default_factory=lambda: {name: [] for name in _SIGNALS}
    )
    imports: dict[str, int] = field(default_factory=lambda: {name: 0 for name in _SIGNALS})

    def add(self, framework: str, weight: float, label: str, source: str) -> None:
        self.scores[framework] += weight
        bucket = self.evidence[framework]
        if len(bucket) < _MAX_EVIDENCE_PER_FRAMEWORK:
            bucket.append(f"{source}: {label}")


def _iter_python_files(root: Path, max_files: int, max_bytes: int):
    import os

    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            seen += 1
            if seen > max_files:
                return
            yield path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def detect_framework(
    project_path: str | Path,
    *,
    max_files: int = 20_000,
    max_file_bytes: int = 2_000_000,
) -> DetectionResult:
    """Detect the most likely Python web framework for ``project_path``."""

    root = resolve_project_path(project_path)
    if not root.exists():
        return DetectionResult(
            framework="unknown",
            confidence=0.0,
            supported=False,
            reason=f"Project path does not exist: {root}",
        )
    if not root.is_dir():
        return DetectionResult(
            framework="unknown",
            confidence=0.0,
            supported=False,
            reason=f"Project path is not a directory: {root}",
        )

    acc = _Accumulator()

    manage_py = root / "manage.py"
    if manage_py.is_file():
        acc.add("django", 6.0, "manage.py present", "project")

    for path in _iter_python_files(root, max_files, max_file_bytes):
        text = _read_text(path)
        if not text:
            continue
        rel = str(path.relative_to(root))
        for framework, signals in _SIGNALS.items():
            for signal in signals:
                matches = signal.pattern.findall(text)
                if not matches:
                    continue
                # Diminishing returns for repeated identical signals.
                occurrences = min(len(matches), 5)
                acc.add(framework, signal.weight * (1 + 0.1 * (occurrences - 1)), signal.label, rel)
            if _IMPORT_MARKERS[framework].search(text):
                acc.imports[framework] += 1

    scores = acc.scores
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_framework, best_score = ranked[0]

    if best_score < 3.0:
        return DetectionResult(
            framework="unknown",
            confidence=0.0,
            supported=False,
            scores={name: round(value, 2) for name, value in scores.items()},
            reason="No strong Django, Flask or FastAPI signals were found.",
        )

    # Import evidence is decisive when heuristics collide (e.g. Flask 2.0 and
    # FastAPI both support ``@app.get``).
    import_ranked = sorted(acc.imports.items(), key=lambda item: item[1], reverse=True)
    if import_ranked and import_ranked[0][1] > 0:
        imported_framework, imported_count = import_ranked[0]
        if imported_count >= 1 and (
            imported_framework != best_framework
            and scores[imported_framework] >= best_score * 0.5
        ):
            best_framework, best_score = imported_framework, scores[imported_framework]

    total = sum(scores.values()) or 1.0
    share = best_score / total
    confidence = round(min(0.99, 0.35 + 0.65 * share), 3) if best_score >= 4 else round(share, 3)

    return DetectionResult(
        framework=best_framework,
        confidence=confidence,
        supported=_SUPPORTED.get(best_framework, False),
        evidence=acc.evidence.get(best_framework, []),
        scores={name: round(value, 2) for name, value in scores.items()},
        reason=(
            f"Detected {best_framework} with score {best_score:.1f} "
            f"(imports: {acc.imports[best_framework]})."
        ),
    )
