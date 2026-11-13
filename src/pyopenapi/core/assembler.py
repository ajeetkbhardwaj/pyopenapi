"""Deterministic assembly of agent reasoning into an OpenAPI document.

The assembler is deliberately boring: structure comes from the normalized IR,
text comes from agent reasoning. It does not synthesize missing endpoints,
schemas or responses.
"""

from __future__ import annotations

from typing import Any

from ..ir.models import (
    NormalizedComponent,
    NormalizedOperation,
    NormalizedParameter,
    NormalizedProject,
    NormalizedResponse,
)
from .reasoners import ComponentReasoning, EndpointReasoning, ProjectMetadata
from .schema_builder import component_schema, ref


class AssemblyError(RuntimeError):
    """Raised when agent output cannot be assembled without losing structure."""


def _annotate(schema: dict[str, Any], *, description: str | None, example: Any, openapi_version: str) -> dict[str, Any]:
    if description is None and example is None:
        return schema
    if "$ref" in schema and openapi_version.startswith("3.0"):
        result: dict[str, Any] = {"allOf": [{"$ref": schema["$ref"]}]}
    else:
        result = dict(schema)
    if description:
        result["description"] = description
    if example is not None:
        result["example"] = example
    return result


def _parameter(
    parameter: NormalizedParameter,
    *,
    patch: dict[str, str],
    openapi_version: str,
) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    if parameter.type:
        schema["type"] = parameter.type
    if parameter.format:
        schema["format"] = parameter.format
    if not schema:
        schema = {"type": "string"}
    description = patch.get(parameter.name) or parameter.description
    result: dict[str, Any] = {
        "name": parameter.name,
        "in": parameter.location,
        "required": bool(parameter.required if parameter.required is not None else parameter.location == "path"),
        "schema": schema,
    }
    if description:
        result["description"] = description
    return result


def _request_body(operation: NormalizedOperation, patch: Any) -> dict[str, Any] | None:
    request = operation.request
    if request is None:
        return None
    schema = ref(request.model_ref) if request.model_ref else (request.schema_data or {"type": "object"})
    body: dict[str, Any] = {
        "required": bool(request.required),
        "content": {request.content_type or "application/json": {"schema": schema}},
    }
    if patch is not None and patch.request_description:
        body["description"] = patch.request_description
    return body


def _response(
    response: NormalizedResponse,
    *,
    description: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"description": description or response.description or "Response"}
    if response.status == "204":
        return result
    if response.model_ref:
        schema: dict[str, Any] = ref(response.model_ref)
        if response.many:
            schema = {"type": "array", "items": schema}
    else:
        schema = response.schema_data or {"type": "object"}
    result["content"] = {response.content_type or "application/json": {"schema": schema}}
    return result


def _operation(
    operation: NormalizedOperation,
    *,
    patch: Any,
    openapi_version: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operationId": operation.operation_id,
        "responses": {},
    }
    summary = patch.summary or operation.summary
    description = patch.description or operation.description
    tags = patch.tags or operation.tags
    if summary:
        result["summary"] = summary
    if description:
        result["description"] = description
    if tags:
        result["tags"] = tags
    if operation.deprecated:
        result["deprecated"] = True
    if operation.parameters:
        result["parameters"] = [
            _parameter(item, patch=patch.parameters, openapi_version=openapi_version)
            for item in operation.parameters
        ]
    body = _request_body(operation, patch)
    if body is not None:
        result["requestBody"] = body
    if operation.security:
        result["security"] = operation.security
    for response in operation.responses:
        result["responses"][response.status] = _response(
            response, description=patch.responses.get(response.status)
        )
    if not result["responses"]:
        raise AssemblyError(
            f"Operation {operation.method.upper()} has no responses after assembly."
        )
    return result


def _component(
    component: NormalizedComponent,
    *,
    reasoning: ComponentReasoning,
    openapi_version: str,
) -> dict[str, Any]:
    schema = component_schema(
        component,
        openapi_version=openapi_version,
        description=reasoning.description,
    )
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, patch in reasoning.fields.items():
            current = properties.get(name)
            if not isinstance(current, dict):
                continue
            properties[name] = _annotate(
                current,
                description=patch.description,
                example=patch.example,
                openapi_version=openapi_version,
            )
    return schema


def assemble_openapi(
    project: NormalizedProject,
    *,
    endpoint_reasonings: dict[str, EndpointReasoning],
    component_reasonings: dict[str, ComponentReasoning],
    metadata: ProjectMetadata,
    openapi_version: str = "3.0.3",
    strict: bool = True,
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    security_schemes: dict[str, Any] = {}

    for endpoint in project.endpoints:
        reasoning = endpoint_reasonings.get(endpoint.endpoint_id)
        if reasoning is None:
            if strict:
                raise AssemblyError(f"Missing endpoint reasoning for {endpoint.path}.")
            continue
        path_item = paths.setdefault(endpoint.path, {})
        for operation in endpoint.operations:
            patch = reasoning.operations.get(operation.method.lower())
            if patch is None:
                if strict:
                    raise AssemblyError(
                        f"Missing {operation.method.upper()} reasoning for {endpoint.path}."
                    )
                continue
            path_item[operation.method.lower()] = _operation(
                operation, patch=patch, openapi_version=openapi_version
            )
            for name, scheme in operation.security_schemes.items():
                security_schemes.setdefault(str(name), scheme)
        if not path_item:
            paths.pop(endpoint.path, None)

    schemas: dict[str, Any] = {}
    for name, component in project.components.items():
        reasoning = component_reasonings.get(component.component_id)
        if reasoning is None:
            if strict:
                raise AssemblyError(f"Missing component reasoning for {name}.")
            continue
        schemas[name] = _component(
            component, reasoning=reasoning, openapi_version=openapi_version
        )

    info: dict[str, Any] = {
        "title": metadata.title,
        "version": metadata.version or "0.1.0",
    }
    if metadata.description:
        info["description"] = metadata.description

    spec: dict[str, Any] = {
        "openapi": openapi_version,
        "info": info,
        "paths": paths,
    }
    if schemas or security_schemes:
        components: dict[str, Any] = {}
        if schemas:
            components["schemas"] = schemas
        if security_schemes:
            components["securitySchemes"] = security_schemes
        spec["components"] = components
    if metadata.tags:
        spec["tags"] = [{"name": tag} for tag in metadata.tags]
    return spec
