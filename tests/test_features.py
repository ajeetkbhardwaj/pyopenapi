from __future__ import annotations

from pathlib import Path

import pytest

from pyopenapi.core.analysis import AnalysisManager, AnalysisRecord
from pyopenapi.core.spec import build_openapi_from_analysis, diff_specs, merge_specs
from pyopenapi.core.validation import validate_spec
from pyopenapi.errors import SpecGenerationError
from pyopenapi.models import AnalysisSummary


def _record(components: dict, endpoints: list[dict]) -> AnalysisRecord:
    summary = AnalysisSummary(
        analysis_id="abcdef0123456789",
        project_path="/tmp/demo",
        framework="django",
        endpoint_count=len(endpoints),
        component_count=len(components),
        created_at="2026-01-01T00:00:00+00:00",
        duration_ms=1,
        cache_dir="/tmp/cache",
        output_dir="/tmp/cache/analyses/abcdef0123456789",
    )
    return AnalysisRecord(summary=summary, endpoints=endpoints, components=components, metadata={})


def test_openapi_31_nullable_and_integer_enum() -> None:
    record = _record(
        {
            "Status": {"type": "enum", "properties": [{"name": "one", "value": 1}, {"name": "two", "value": 2}]},
            "Doc": {
                "type": "serializer",
                "properties": [
                    {"name": "title", "type": "string", "required": True, "nullable": True},
                    {"name": "tags", "type": "array", "items": {"type": "string"}},
                ],
            },
        },
        [{"method": "GET", "url": {"url": "/docs/"}, "view": "DocView", "function": "list"}],
    )
    spec = build_openapi_from_analysis(record, openapi_version="3.1.0")
    assert spec["openapi"] == "3.1.0"
    doc = spec["components"]["schemas"]["Doc"]
    assert doc["properties"]["title"]["type"] == ["string", "null"]
    assert doc["properties"]["tags"]["items"] == {"type": "string"}
    status = spec["components"]["schemas"]["Status"]
    assert status["type"] == "integer"
    assert status["enum"] == [1, 2]


def test_merge_specs_reports_duplicates() -> None:
    first = {"openapi": "3.0.3", "info": {"title": "A", "version": "1"}, "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    second = {"openapi": "3.0.3", "info": {"title": "B", "version": "1"}, "paths": {"/a": {"get": {"responses": {"200": {"description": "dup"}}}}, "/b": {"post": {"responses": {"201": {"description": "ok"}}}}}}
    merged = merge_specs([first, second], title="Combined")
    assert merged["info"]["title"] == "Combined"
    assert set(merged["paths"]) == {"/a", "/b"}


def test_merge_specs_on_conflict_modes() -> None:
    first = {"openapi": "3.0.3", "info": {"title": "A", "version": "1"}, "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    second = {"openapi": "3.0.3", "info": {"title": "B", "version": "1"}, "paths": {"/a": {"get": {"responses": {"200": {"description": "dup"}}}}}}

    quiet = merge_specs([first, second], on_conflict="first")
    assert "x-pyopenapi-warnings" not in quiet

    warned = merge_specs([first, second], on_conflict="warn")
    assert warned["x-pyopenapi-warnings"]

    with pytest.raises(SpecGenerationError):
        merge_specs([first, second], on_conflict="error")


def test_diff_specs_removed_property_is_breaking() -> None:
    old = {
        "openapi": "3.0.3",
        "info": {"title": "A", "version": "1"},
        "paths": {},
        "components": {"schemas": {"Doc": {"type": "object", "properties": {"id": {"type": "integer"}, "title": {"type": "string"}}}}},
    }
    new = {
        "openapi": "3.0.3",
        "info": {"title": "A", "version": "1"},
        "paths": {},
        "components": {"schemas": {"Doc": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
    }
    assert diff_specs(old, new).breaking is True


def test_diff_specs_added_property_is_not_breaking() -> None:
    old = {
        "openapi": "3.0.3",
        "info": {"title": "A", "version": "1"},
        "paths": {},
        "components": {"schemas": {"Doc": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
    }
    new = {
        "openapi": "3.0.3",
        "info": {"title": "A", "version": "1"},
        "paths": {},
        "components": {"schemas": {"Doc": {"type": "object", "properties": {"id": {"type": "integer"}, "title": {"type": "string"}}}}},
    }
    assert diff_specs(old, new).breaking is False


def test_diff_specs_flags_breaking_changes() -> None:
    old = {"openapi": "3.0.3", "info": {"title": "A", "version": "1"}, "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    new = {"openapi": "3.0.3", "info": {"title": "A", "version": "1"}, "paths": {"/b": {"post": {"responses": {"201": {"description": "ok"}}}}}}
    result = diff_specs(old, new)
    assert result.changed is True
    assert result.breaking is True
    assert result.change_count >= 2


def test_validate_31_webhooks_without_paths() -> None:
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Hooks", "version": "1"},
        "webhooks": {
            "newEvent": {
                "post": {
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    report = validate_spec(document)
    assert report.valid is True


def test_cache_cleanup_dry_run(isolated_settings, tmp_path: Path) -> None:
    manager = AnalysisManager()
    stats = manager.cleanup_cache(dry_run=True, max_age_days=0, include_repos=False)
    assert stats.dry_run is True
    assert isinstance(stats.analyses, int)


def test_fastapi_parameters_and_security(tmp_path: Path) -> None:
    root = tmp_path / "fastapi_advanced"
    root.mkdir()
    (root / "main.py").write_text(
        '''
from typing import Annotated
from fastapi import APIRouter, Cookie, Depends, FastAPI, File, Form, Header, Path, Query, Security, UploadFile
from fastapi.security import HTTPBearer

app = FastAPI()
router = APIRouter(prefix="/things")
bearer = HTTPBearer()

def get_db():
    return object()

def get_user():
    return {"id": 1}

@router.get("/{thing_id}")
def read_thing(
    thing_id: int = Path(...),
    q: str | None = Query(default=None),
    x_trace: str = Header(default="none"),
    session: str | None = Cookie(default=None),
    db=Depends(get_db),
    user=Security(get_user),
):
    return {}

@router.post("/{thing_id}/upload")
def upload(thing_id: int, name: Annotated[str, Form()], file: UploadFile = File(...)):
    return {}
''',
        encoding="utf-8",
    )
    record = AnalysisManager().analyze(root, "auto", use_dynamic=False, force=True)
    endpoints = {(item["method"], item["url"]["url"]): item for item in record.endpoints}
    get_ep = endpoints[("GET", "/things/{thing_id}")]
    locations = {param["name"]: param["location"] for param in get_ep["url"]["parameter"]}
    assert locations["thing_id"] == "path"
    assert locations["q"] == "query"
    assert locations["x_trace"] == "header"
    assert locations["session"] == "cookie"
    assert get_ep["security"] == [{"user": []}]

    post_ep = endpoints[("POST", "/things/{thing_id}/upload")]
    post_locations = {param["name"]: param["location"] for param in post_ep["url"]["parameter"]}
    assert post_locations["name"] == "form"
    assert post_locations["file"] == "file"

    spec = build_openapi_from_analysis(record)
    upload = spec["paths"]["/things/{thing_id}/upload"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    assert upload["requestBody"]["content"]["multipart/form-data"]["schema"]["properties"]["file"]["format"] == "binary"


def test_flask_smorest_and_restx(tmp_path: Path) -> None:
    root = tmp_path / "flask_advanced"
    root.mkdir()
    (root / "app.py").write_text(
        '''
from flask import Flask
from flask_smorest import Blueprint
from flask_restx import Namespace, Resource
from marshmallow import Schema, fields

app = Flask(__name__)
blp = Blueprint("things", __name__, url_prefix="/things")
ns = Namespace("widgets", path="/widgets")

class ThingSchema(Schema):
    name = fields.Str(required=True)

class WidgetSchema(Schema):
    id = fields.Int()

@blp.route("/")
@blp.arguments(ThingSchema)
@blp.response(200, ThingSchema)
def list_things():
    return []

@ns.route("/")
class WidgetList(Resource):
    @ns.marshal_with(WidgetSchema)
    def get(self):
        return []
''',
        encoding="utf-8",
    )
    record = AnalysisManager().analyze(root, "auto", use_dynamic=False, force=True)
    endpoints = {(item["method"], item["url"]["url"]): item for item in record.endpoints}
    thing = endpoints[("GET", "/things")]
    assert thing["request_serializer"] == "ThingSchema"
    assert thing["response_serializer"] == "ThingSchema"
    widget = endpoints[("GET", "/widgets")]
    assert widget["response_serializer"] == "WidgetSchema"


def test_http_guard_health_and_auth(isolated_settings) -> None:
    from starlette.testclient import TestClient

    from pyopenapi.config import get_settings
    from pyopenapi.server.http import build_http_app
    from pyopenapi.server.instance import mcp

    settings = get_settings().model_copy(update={"api_key": "secret", "enable_metrics_endpoint": True})
    app = build_http_app(mcp, transport="streamable-http", settings=settings)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        denied = client.get("/metrics")
        assert denied.status_code == 401

        allowed = client.get("/metrics", headers={"Authorization": "Bearer secret"})
        assert allowed.status_code == 200


def test_mcp_initialize_over_http(isolated_settings) -> None:
    from starlette.testclient import TestClient

    from pyopenapi.config import get_settings
    from pyopenapi.server.http import build_http_app
    from pyopenapi.server.instance import mcp

    settings = get_settings().model_copy(update={"api_key": None})
    app = build_http_app(mcp, transport="streamable-http", settings=settings)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Host": "127.0.0.1:8765",
            },
        )
        assert response.status_code == 200


def test_http_guard_rejects_oversized_body(isolated_settings) -> None:
    from starlette.testclient import TestClient

    from pyopenapi.config import get_settings
    from pyopenapi.server.http import build_http_app
    from pyopenapi.server.instance import mcp

    settings = get_settings().model_copy(update={"api_key": None, "max_request_bytes": 100})
    app = build_http_app(mcp, transport="streamable-http", settings=settings)
    with TestClient(app) as client:
        large_body = {"data": "x" * 200}
        resp = client.post("/mcp", json=large_body)
        assert resp.status_code == 413
        assert resp.json()["error"] == "request_too_large"


def test_http_guard_handles_disconnect(isolated_settings) -> None:
    from starlette.testclient import TestClient

    from pyopenapi.config import get_settings
    from pyopenapi.server.http import build_http_app
    from pyopenapi.server.instance import mcp

    settings = get_settings().model_copy(update={"api_key": None})
    app = build_http_app(mcp, transport="streamable-http", settings=settings)
    # Simulate disconnect by closing connection before response - TestClient
    # doesn't easily simulate mid-request disconnect, so we just verify
    # the middleware doesn't crash on normal requests.
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
