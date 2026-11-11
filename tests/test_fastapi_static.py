from __future__ import annotations

from pathlib import Path

from pyopenapi.analyzers.fastapi_static import analyze_fastapi


def test_extracts_router_prefix_and_models(fastapi_project: Path) -> None:
    raw = analyze_fastapi(fastapi_project)
    assert raw.framework == "fastapi"
    assert raw.warnings == []

    by_route = {item["url"]["url"]: item for item in raw.endpoints}
    assert "/items/{item_id}" in by_route
    assert "/items" in by_route

    detail = by_route["/items/{item_id}"]
    assert detail["response_model"] == "Item"
    params = {item["name"]: item for item in detail["url"]["parameter"]}
    assert params["item_id"]["type"] == "integer"
    assert params["q"]["location"] == "query"

    create = by_route["/items"]
    assert create["request_model"] == "ItemCreate"
    assert create["response_model"] == "Item"


def test_extracts_pydantic_components(fastapi_project: Path) -> None:
    raw = analyze_fastapi(fastapi_project)
    assert {"Item", "ItemCreate"} <= set(raw.components)
    item_fields = {field["name"]: field for field in raw.components["Item"]["properties"]}
    assert item_fields["price"]["type"] == "number"
    assert item_fields["price"]["format"] == "float"
