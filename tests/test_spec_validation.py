from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pyopenapi.core.analysis import AnalysisManager
from pyopenapi.core.spec import build_openapi_from_analysis, write_spec
from pyopenapi.core.validation import validate_spec
from pyopenapi.errors import SpecValidationError


def test_analysis_cache_and_static_spec(django_project: Path, isolated_settings) -> None:
    manager = AnalysisManager()
    record = manager.analyze(django_project, "auto", use_dynamic=False, force=True)

    assert record.summary.framework == "django"
    assert record.summary.endpoint_count == 8
    assert record.summary.component_count >= 2
    assert record.summary.options["endpoint_source"] == "static"

    cached = manager.load(record.summary.analysis_id)
    assert cached.summary.analysis_id == record.summary.analysis_id
    assert cached.endpoints == record.endpoints

    spec = build_openapi_from_analysis(record)
    assert spec["openapi"] == "3.0.3"
    assert "/api/documents/" in spec["paths"]
    assert "DocumentSerializer" in spec["components"]["schemas"]

    create = spec["paths"]["/api/documents/"]["post"]
    assert create["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentSerializer"
    }

    list_operation = spec["paths"]["/api/documents/"]["get"]
    response_schema = list_operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/DocumentSerializer"},
    }

    report = validate_spec(spec)
    assert report.valid is True
    assert report.error_count == 0


def test_write_spec_yaml_and_json(tmp_path: Path) -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Demo", "version": "1.0.0"},
        "paths": {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    yml_path = write_spec(spec, tmp_path, fmt="yml")
    yaml_path = write_spec(spec, tmp_path, fmt="yaml")
    json_path = write_spec(spec, tmp_path, fmt="json")
    assert yml_path.exists() and yml_path.suffix == ".yml"
    assert yaml_path.exists() and yaml_path.suffix == ".yaml"
    assert json_path.exists() and json_path.suffix == ".json"
    assert validate_spec(yml_path).valid is True
    assert validate_spec(yaml_path).valid is True
    assert validate_spec(json_path).valid is True


def test_validation_reports_missing_path_parameter(tmp_path: Path) -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Broken", "version": "1.0.0"},
        "paths": {"/items/{item_id}": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    report = validate_spec(spec)
    assert report.valid is False
    assert any("item_id" in issue.message for issue in report.issues)


def test_strict_validation_reports_or_requires_optional_dependency() -> None:
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Strict", "version": "1"},
        "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    if importlib.util.find_spec("openapi_spec_validator") is None:
        with pytest.raises(SpecValidationError):
            validate_spec(document, strict=True)
    else:
        assert validate_spec(document, strict=True).valid is True
