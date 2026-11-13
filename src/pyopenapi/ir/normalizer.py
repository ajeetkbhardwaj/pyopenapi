"""Shared IR normalization helpers.

Framework adapters only need to declare their framework name and call
:func:`normalize_project`; the mapping from raw analyzer dictionaries to the
IR lives here so every framework produces the same shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..utils import slugify, utc_now_iso
from .models import (
    Confidence,
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

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
_PARAM_LOCATIONS = {"path", "query", "header", "cookie"}
_BODY_LOCATIONS = {"body", "form", "file"}
_FIELD_KEYS = {
    "name",
    "type",
    "format",
    "ref",
    "enum",
    "items",
    "additional_properties",
    "max_length",
    "required",
    "nullable",
    "read_only",
    "write_only",
    "description",
    "default",
    "source",
}


class ProjectNormalizer(Protocol):
    framework: str

    def normalize(
        self, record: Any, project_path: str | Path | None = None
    ) -> NormalizedProject:  # pragma: no cover - protocol
        ...


class BaseNormalizer:
    framework = "unknown"

    def normalize(
        self, record: Any, project_path: str | Path | None = None
    ) -> NormalizedProject:
        return normalize_project(
            record, framework=self.framework, project_path=project_path
        )


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _endpoint_path(raw: dict[str, Any]) -> str:
    url = raw.get("url")
    path = url.get("url") if isinstance(url, dict) else url
    path = str(path or "").strip()
    if not path:
        return ""
    return path if path.startswith("/") else f"/{path}"


def _raw_parameters(raw: dict[str, Any]) -> list[dict[str, Any]]:
    url = raw.get("url")
    items = url.get("parameter", []) if isinstance(url, dict) else []
    return [item for item in items or [] if isinstance(item, dict) and item.get("name")]


def _parameter(item: dict[str, Any]) -> NormalizedParameter:
    schema: dict[str, Any] = {}
    if item.get("type"):
        schema["type"] = item["type"]
    if item.get("format"):
        schema["format"] = item["format"]
    known = {"name", "location", "required", "type", "format", "description"}
    extra = {key: value for key, value in item.items() if key not in known}
    return NormalizedParameter(
        name=str(item["name"]),
        location=str(item.get("location", "query")),
        required=bool(item.get("required", item.get("location") == "path")),
        type=item.get("type"),
        format=item.get("format"),
        description=item.get("description"),
        schema_data=schema,
        metadata=extra,
    )


def _inline_request(parameters: list[dict[str, Any]]) -> NormalizedRequest | None:
    """Build a request body from inline body/form/file parameters.

    A body is emitted whenever at least one body-located parameter exists. The
    body itself is marked required only when at least one of its fields is
    required; otherwise clients may omit it entirely.
    """

    body = [item for item in parameters if item.get("location") in _BODY_LOCATIONS]
    if not body:
        return None
    properties: dict[str, Any] = {}
    required: list[str] = []
    has_file = False
    for item in body:
        location = item.get("location")
        field_schema: dict[str, Any] = (
            {"type": "string", "format": "binary"}
            if location == "file"
            else {"type": item.get("type") or "string"}
        )
        properties[str(item["name"])] = field_schema
        if location == "file":
            has_file = True
        if item.get("required"):
            required.append(str(item["name"]))
    if has_file:
        content_type = "multipart/form-data"
    elif any(item.get("location") == "form" for item in body):
        content_type = "application/x-www-form-urlencoded"
    else:
        content_type = "application/json"
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return NormalizedRequest(
        required=bool(required),
        content_type=content_type,
        schema_data=schema,
    )


def _request(raw: dict[str, Any]) -> NormalizedRequest | None:
    model_ref = raw.get("request_serializer") or raw.get("request_model")
    if model_ref:
        return NormalizedRequest(model_ref=str(model_ref), required=True)
    return _inline_request(_raw_parameters(raw))


def _success_status(method: str, function: str | None) -> str:
    if method == "post" and function in {"create"}:
        return "201"
    if method == "delete":
        return "204"
    return "200"


def _response(raw: dict[str, Any], method: str, function: str | None) -> NormalizedResponse:
    status = str(raw.get("success_status") or _success_status(method, function))
    model_ref = raw.get("response_serializer") or raw.get("response_model")
    return NormalizedResponse(
        status=status,
        description="No content" if status == "204" else "Successful response",
        model_ref=str(model_ref) if model_ref else None,
        many=bool(raw.get("response_many")),
    )


def _operation_id(method: str, path: str) -> str:
    return f"{method}_{slugify(path.strip('/') or 'root').replace('-', '_')}"


def _operation(raw: dict[str, Any], path: str) -> NormalizedOperation | None:
    method = str(raw.get("method", "")).lower()
    if method not in _HTTP_METHODS:
        return None
    view = raw.get("view")
    function = raw.get("function")
    parameters = [_parameter(item) for item in _raw_parameters(raw)]
    operation_parameters = [
        item for item in parameters if item.location in _PARAM_LOCATIONS
    ]
    tags = [str(tag) for tag in raw.get("tags") or [] if tag]
    if not tags:
        tags = [path.strip("/").split("/")[0] or "default"]
    summary = raw.get("summary")
    if not summary:
        summary = f"{view}.{function}" if view and function and view != function else (view or function)
    security = raw.get("security")
    security_schemes = raw.get("security_schemes")
    metadata = {
        "is_viewset": bool(raw.get("is_viewset")),
        "response_annotation": raw.get("response_annotation"),
        "request_model": raw.get("request_model"),
        "response_model": raw.get("response_model"),
        "request_serializer": raw.get("request_serializer"),
        "response_serializer": raw.get("response_serializer"),
        "response_many": bool(raw.get("response_many")),
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, False)}
    return NormalizedOperation(
        method=method,
        operation_id=str(
            raw.get("operation_id") or raw.get("operationId") or _operation_id(method, path)
        ),
        summary=str(summary) if summary else None,
        description=str(raw["description"]) if raw.get("description") else None,
        tags=tags,
        parameters=operation_parameters,
        request=_request(raw),
        responses=[_response(raw, method, function)],
        security=security if isinstance(security, list) else [],
        security_schemes=security_schemes if isinstance(security_schemes, dict) else {},
        deprecated=bool(raw.get("deprecated", False)),
        source_refs=[
            SourceRef(path=str(raw["path"]) if raw.get("path") else None, symbol=function or view)
        ],
        metadata=metadata,
    )


def normalize_endpoints(
    raw_endpoints: list[dict[str, Any]],
    framework: str,
    *,
    warnings: list[str] | None = None,
) -> list[NormalizedEndpoint]:
    warnings = warnings if warnings is not None else []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_endpoints:
        if not isinstance(raw, dict):
            continue
        path = _endpoint_path(raw)
        if path:
            grouped.setdefault(path, []).append(raw)

    endpoints: list[NormalizedEndpoint] = []
    for path in sorted(grouped):
        operations: dict[str, NormalizedOperation] = {}
        for raw in grouped[path]:
            operation = _operation(raw, path)
            if operation is not None:
                if operation.method in operations:
                    warnings.append(
                        f"Duplicate operation {operation.method.upper()} {path} was skipped "
                        f"(kept {operations[operation.method].operation_id}, "
                        f"dropped {operation.operation_id})."
                    )
                    continue
                operations[operation.method] = operation
        if not operations:
            continue
        ordered = [operations[method] for method in sorted(operations)]
        tags: list[str] = []
        source_refs: list[SourceRef] = []
        for operation in ordered:
            for tag in operation.tags:
                if tag not in tags:
                    tags.append(tag)
            source_refs.extend(operation.source_refs)
        endpoints.append(
            NormalizedEndpoint(
                endpoint_id=stable_id("endpoint", framework, path),
                framework=framework,
                path=path,
                operations=ordered,
                tags=tags,
                source_refs=source_refs,
                confidence=Confidence(score=1.0, source="static"),
            )
        )
    return endpoints


def _field(raw: dict[str, Any]) -> NormalizedField | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    data = {key: raw[key] for key in _FIELD_KEYS if key in raw}
    metadata = {key: value for key, value in raw.items() if key not in _FIELD_KEYS}
    return NormalizedField(**data, metadata=metadata)


def _component(name: str, raw: dict[str, Any]) -> NormalizedComponent:
    kind = str(raw.get("type") or raw.get("kind") or "unknown")
    properties = raw.get("properties") or raw.get("fields") or []
    fields = [
        field
        for field in (_field(item) for item in properties if isinstance(item, dict))
        if field is not None
    ]
    dependencies: list[str] = []
    for dependency in raw.get("dependencies") or []:
        value = dependency.get("name") if isinstance(dependency, dict) else dependency
        if value and str(value) not in dependencies:
            dependencies.append(str(value))
    known = {
        "type",
        "kind",
        "properties",
        "fields",
        "dependencies",
        "path",
        "source_file",
        "parent_classes",
        "supports_request",
        "supports_response",
    }
    metadata = {key: value for key, value in raw.items() if key not in known}
    source_path = raw.get("path") or raw.get("source_file")
    return NormalizedComponent(
        component_id=stable_id("component", name),
        name=name,
        kind=kind,
        source_ref=SourceRef(path=str(source_path) if source_path else None, symbol=name),
        parent_classes=[str(item) for item in raw.get("parent_classes") or []],
        fields=fields,
        dependencies=dependencies,
        supports_request=raw.get("supports_request"),
        supports_response=raw.get("supports_response"),
        confidence=Confidence(score=1.0, source="static"),
        metadata=metadata,
    )


def normalize_project(
    record: Any,
    *,
    framework: str | None = None,
    project_path: str | Path | None = None,
    title: str | None = None,
) -> NormalizedProject:
    summary = _get(record, "summary")
    resolved_framework = framework or str(_get(summary, "framework", "unknown"))
    resolved_path = str(
        project_path or _get(summary, "project_path", "") or ""
    )
    analysis_id = _get(summary, "analysis_id")
    components_raw = _get(record, "components", {}) or {}
    components = {
        str(name): _component(str(name), raw)
        for name, raw in components_raw.items()
        if isinstance(raw, dict)
    }
    warnings = [str(item) for item in _get(summary, "warnings", []) or []]
    endpoints = normalize_endpoints(
        _get(record, "endpoints", []) or [],
        resolved_framework,
        warnings=warnings,
    )
    return NormalizedProject(
        project_id=stable_id("project", analysis_id or resolved_path, resolved_framework),
        analysis_id=str(analysis_id) if analysis_id else None,
        project_path=resolved_path,
        framework=resolved_framework,
        title=title,
        endpoints=endpoints,
        components=components,
        warnings=warnings,
        created_at=str(_get(summary, "created_at") or utc_now_iso()),
        confidence=Confidence(score=1.0, source="static"),
        metadata=dict(_get(record, "metadata", {}) or {}),
    )
