from __future__ import annotations

from pathlib import Path

from pyopenapi.analyzers.flask_static import analyze_flask


def test_extracts_flask_routes(flask_project: Path) -> None:
    raw = analyze_flask(flask_project)
    assert raw.framework == "flask"
    assert raw.warnings == []

    found = {(item["method"], item["url"]["url"]) for item in raw.endpoints}
    assert ("GET", "/users") in found
    assert ("POST", "/users") in found
    assert ("GET", "/users/{user_id}") in found
    assert ("PUT", "/users/{user_id}") in found


def test_infers_flask_path_parameter_type(flask_project: Path) -> None:
    raw = analyze_flask(flask_project)
    detail = next(item for item in raw.endpoints if item["url"]["url"] == "/users/{user_id}")
    parameter = detail["url"]["parameter"][0]
    assert parameter["name"] == "user_id"
    assert parameter["type"] == "integer"


def test_extracts_pydantic_components(flask_project: Path) -> None:
    raw = analyze_flask(flask_project)
    assert "User" in raw.components
    fields = {field["name"]: field for field in raw.components["User"]["properties"]}
    assert fields["id"]["type"] == "integer"
    assert fields["email"]["required"] is False
