from __future__ import annotations

from pathlib import Path

from pyopenapi.analyzers.base import AnalysisOptions
from pyopenapi.analyzers.django import analyze_django
from pyopenapi.analyzers.django_components_static import enrich_django_components
from pyopenapi.analyzers.django_static import extract_django_endpoints


def test_extracts_viewset_actions_and_function_views(django_project: Path) -> None:
    endpoints, warnings = extract_django_endpoints(django_project)
    assert warnings == []

    found = {(item["method"], item["url"]["url"]) for item in endpoints}
    assert ("GET", "/api/documents/") in found
    assert ("POST", "/api/documents/") in found
    assert ("GET", "/api/documents/{pk}/") in found
    assert ("DELETE", "/api/documents/{pk}/") in found
    assert ("POST", "/api/documents/{pk}/publish/") in found
    assert ("GET", "/health") in found


def test_attaches_serializer_models(django_project: Path) -> None:
    endpoints, _ = extract_django_endpoints(django_project)
    list_endpoint = next(
        item for item in endpoints if item["method"] == "GET" and item["url"]["url"] == "/api/documents/"
    )
    create_endpoint = next(
        item for item in endpoints if item["method"] == "POST" and item["url"]["url"] == "/api/documents/"
    )
    assert list_endpoint["response_serializer"] == "DocumentSerializer"
    assert list_endpoint["response_many"] is True
    assert create_endpoint["request_serializer"] == "DocumentSerializer"


def test_enriches_serializer_fields_from_model(django_project: Path) -> None:
    serializer_path = django_project / "app" / "serializers.py"
    components = {
        "DocumentSerializer": {
            "name": "DocumentSerializer",
            "type": "serializer",
            "path": str(serializer_path),
            "properties": [],
        }
    }
    enriched, warnings = enrich_django_components(django_project, components)
    assert warnings == []

    fields = {field["name"]: field for field in enriched["DocumentSerializer"]["properties"]}
    assert fields["id"] == {"name": "id", "type": "integer", "read_only": True}
    assert fields["title"]["type"] == "string"
    assert fields["title"]["max_length"] == 255
    assert fields["created_at"]["format"] == "date-time"
    assert fields["created_at"]["read_only"] is True
    assert "Document" in enriched


def test_analyze_django_without_legacy_analyzers(
    django_project: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("PYOPENAPI_KNOWL_ROOT", raising=False)
    monkeypatch.delenv("KNOWL_APIDOCS_ROOT", raising=False)

    raw = analyze_django(
        django_project,
        tmp_path / "out",
        AnalysisOptions(use_dynamic=False),
        None,
    )

    assert raw.metadata["code_analyzer"] == "static"
    assert raw.metadata["endpoint_source"] == "static"
    assert raw.endpoints
    assert "DocumentSerializer" in raw.components
    assert "Document" in raw.components
