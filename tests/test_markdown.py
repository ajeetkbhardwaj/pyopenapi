from __future__ import annotations

from pathlib import Path

from pyopenapi.core.analysis import filter_endpoints
from pyopenapi.core.markdown import generate_markdown
from pyopenapi.core.spec import build_openapi_from_analysis
from pyopenapi.core.analysis import AnalysisManager


def test_generates_multi_file_markdown(django_project: Path, isolated_settings, tmp_path: Path) -> None:
    record = AnalysisManager().analyze(django_project, "auto", use_dynamic=False, force=True)
    spec = build_openapi_from_analysis(record)

    result = generate_markdown(spec, tmp_path / "docs", layout="multi")
    index = Path(result.index_path)
    assert index.exists()
    assert result.file_count > 1
    assert "djproj API" in index.read_text(encoding="utf-8")
    assert (tmp_path / "docs" / "endpoints").is_dir()
    assert (tmp_path / "docs" / "components").is_dir()


def test_generates_single_file_markdown(django_project: Path, isolated_settings, tmp_path: Path) -> None:
    record = AnalysisManager().analyze(django_project, "auto", use_dynamic=False, force=True)
    spec = build_openapi_from_analysis(record)

    result = generate_markdown(spec, tmp_path / "docs", layout="single")
    single = Path(result.index_path)
    assert single.exists()
    text = single.read_text(encoding="utf-8")
    assert "DocumentSerializer" in text
    assert "/api/documents/" in text


def test_filters_endpoints() -> None:
    endpoints = [
        {"method": "GET", "url": {"url": "/items"}, "view": "ItemViewSet", "function": "list"},
        {"method": "POST", "url": {"url": "/items"}, "view": "ItemViewSet", "function": "create"},
        {"method": "GET", "url": {"url": "/health"}, "view": "health", "function": None},
    ]
    assert len(filter_endpoints(endpoints, method="get")) == 2
    assert len(filter_endpoints(endpoints, path_prefix="/items")) == 2
    assert len(filter_endpoints(endpoints, view="health")) == 1
    assert len(filter_endpoints(endpoints, search="create")) == 1
