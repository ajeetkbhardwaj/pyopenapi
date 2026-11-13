from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pyopenapi.analyzers.django_static import extract_django_endpoints
from pyopenapi.analyzers.fastapi_static import analyze_fastapi
from pyopenapi.analyzers.flask_static import analyze_flask
from pyopenapi.ir import normalize_analysis
from pyopenapi.ir.normalizer import normalize_endpoints


def _record(raw, project_path: Path):
    summary = SimpleNamespace(
        analysis_id="test-analysis",
        project_path=str(project_path),
        framework=raw.framework,
        warnings=raw.warnings,
        created_at="2026-01-01T00:00:00+00:00",
    )
    return SimpleNamespace(
        summary=summary,
        endpoints=raw.endpoints,
        components=raw.components,
        metadata=raw.metadata,
    )


def test_django_normalizer_groups_operations_and_models(django_project: Path) -> None:
    endpoints, _ = extract_django_endpoints(django_project)
    raw = SimpleNamespace(
        framework="django",
        endpoints=endpoints,
        components={
            "DocumentSerializer": {
                "type": "serializer",
                "path": str(django_project / "app" / "serializers.py"),
                "properties": [
                    {"name": "id", "type": "integer", "read_only": True},
                    {"name": "title", "type": "string", "max_length": 255, "required": True},
                ],
                "supports_request": True,
                "supports_response": True,
            }
        },
        warnings=[],
        metadata={"endpoint_source": "static"},
    )

    project = normalize_analysis(_record(raw, django_project), framework="django")
    by_path = {endpoint.path: endpoint for endpoint in project.endpoints}

    collection = by_path["/api/documents/"]
    assert {operation.method for operation in collection.operations} == {"get", "post"}
    get_op = next(item for item in collection.operations if item.method == "get")
    post_op = next(item for item in collection.operations if item.method == "post")
    assert get_op.operation_id == "get_api_documents"
    assert get_op.responses[0].model_ref == "DocumentSerializer"
    assert get_op.responses[0].many is True
    assert post_op.request is not None
    assert post_op.request.model_ref == "DocumentSerializer"
    assert post_op.responses[0].status == "201"

    detail = by_path["/api/documents/{pk}/"]
    delete_op = next(item for item in detail.operations if item.method == "delete")
    assert delete_op.responses[0].status == "204"
    assert delete_op.responses[0].model_ref is None
    assert delete_op.source_refs[0].path.endswith("views.py")

    component = project.components["DocumentSerializer"]
    assert component.kind == "serializer"
    assert component.supports_request is True
    assert component.fields[0].name == "id"
    assert component.fields[1].max_length == 255


def test_flask_normalizer_maps_parameters_and_components(flask_project: Path) -> None:
    raw = analyze_flask(flask_project)
    project = normalize_analysis(_record(raw, flask_project), framework="flask")
    by_path = {endpoint.path: endpoint for endpoint in project.endpoints}

    assert {operation.method for operation in by_path["/users"].operations} == {"get", "post"}
    detail = by_path["/users/{user_id}"]
    assert {operation.method for operation in detail.operations} == {"get", "put"}
    get_op = next(item for item in detail.operations if item.method == "get")
    parameter = next(item for item in get_op.parameters if item.name == "user_id")
    assert parameter.location == "path"
    assert parameter.type == "integer"
    assert parameter.required is True

    user = project.components["User"]
    assert user.kind == "pydantic_model"
    fields = {field.name: field for field in user.fields}
    assert fields["id"].type == "integer"
    assert fields["email"].required is False


def test_fastapi_normalizer_maps_request_and_response_models(fastapi_project: Path) -> None:
    raw = analyze_fastapi(fastapi_project)
    project = normalize_analysis(_record(raw, fastapi_project), framework="fastapi")
    by_path = {endpoint.path: endpoint for endpoint in project.endpoints}

    detail = by_path["/items/{item_id}"]
    get_op = next(item for item in detail.operations if item.method == "get")
    assert get_op.responses[0].model_ref == "Item"
    params = {item.name: item for item in get_op.parameters}
    assert params["item_id"].type == "integer"
    assert params["q"].location == "query"

    create = by_path["/items"]
    post_op = next(item for item in create.operations if item.method == "post")
    assert post_op.request is not None
    assert post_op.request.model_ref == "ItemCreate"
    assert post_op.responses[0].model_ref == "Item"

    assert {"Item", "ItemCreate"} <= set(project.components)
    assert project.components["Item"].fields[0].name == "id"


def test_normalize_endpoints_warns_on_duplicate_operation() -> None:
    warnings: list[str] = []
    endpoints = normalize_endpoints(
        [
            {"url": "/items", "method": "get", "view": "ItemView", "function": "list"},
            {"url": "/items", "method": "get", "view": "ItemView", "function": "duplicate"},
        ],
        "django",
        warnings=warnings,
    )

    assert len(endpoints) == 1
    assert len(endpoints[0].operations) == 1
    assert any("Duplicate operation GET /items" in message for message in warnings)


def test_inline_request_optional_body_is_not_required() -> None:
    endpoints = normalize_endpoints(
        [
            {
                "url": {
                    "url": "/notes",
                    "parameter": [{"name": "text", "location": "body", "type": "string"}],
                },
                "method": "post",
                "view": "NoteView",
                "function": "create",
            }
        ],
        "flask",
    )
    request = endpoints[0].operations[0].request
    assert request is not None
    assert request.required is False
    assert request.content_type == "application/json"


def test_inline_request_required_field_marks_body_required() -> None:
    endpoints = normalize_endpoints(
        [
            {
                "url": {
                    "url": "/notes",
                    "parameter": [
                        {"name": "text", "location": "body", "type": "string", "required": True}
                    ],
                },
                "method": "post",
                "view": "NoteView",
                "function": "create",
            }
        ],
        "flask",
    )
    request = endpoints[0].operations[0].request
    assert request is not None
    assert request.required is True


def test_inline_request_file_fields_use_multipart() -> None:
    endpoints = normalize_endpoints(
        [
            {
                "url": {
                    "url": "/upload",
                    "parameter": [
                        {"name": "file", "location": "file", "required": True},
                        {"name": "caption", "location": "form", "type": "string"},
                    ],
                },
                "method": "post",
                "view": "UploadView",
                "function": "create",
            }
        ],
        "flask",
    )
    request = endpoints[0].operations[0].request
    assert request is not None
    assert request.content_type == "multipart/form-data"
    assert request.schema_data["properties"]["file"] == {"type": "string", "format": "binary"}
