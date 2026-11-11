"""OpenAPI parsing and structural validation.

This is intentionally lightweight (no external schema validator) so it works in
minimal environments. It catches the issues that most often break generated
specs: missing required keys, bad refs, malformed paths and duplicate
operationIds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..errors import SpecValidationError
from ..models import ValidationIssue, ValidationReport

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def load_spec(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load an OpenAPI document from a dict, a file path or raw text."""

    if isinstance(source, dict):
        return source

    text: str
    path = Path(str(source))
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = str(source)

    stripped = text.lstrip()
    try:
        if stripped.startswith("{"):
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SpecValidationError(f"Could not parse OpenAPI document: {exc}") from exc

    if not isinstance(data, dict):
        raise SpecValidationError("OpenAPI document must be a mapping/object at the root.")
    return data


def resolve_ref(document: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    current: Any = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def _iter_refs(node: Any, path: str = "#"):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield (path, ref)
        for key, value in node.items():
            if key == "$ref":
                continue
            yield from _iter_refs(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_refs(value, f"{path}/{index}")


def validate_spec(source: str | Path | dict[str, Any]) -> ValidationReport:
    document = load_spec(source)
    issues: list[ValidationIssue] = []

    def add(path: str, message: str, severity: str = "error", code: str | None = None) -> None:
        issues.append(ValidationIssue(path=path, message=message, severity=severity, code=code))

    openapi_version = document.get("openapi")
    if not isinstance(openapi_version, str) or not openapi_version:
        if "swagger" in document:
            add("#/openapi", "Swagger 2.0 documents are not supported; expected OpenAPI 3.x.", "error", "unsupported-version")
        else:
            add("#/openapi", "Missing required 'openapi' version field.", "error", "missing-openapi")
    elif not openapi_version.startswith("3."):
        add("#/openapi", f"Unsupported OpenAPI version '{openapi_version}'. Expected 3.x.", "error", "unsupported-version")

    info = document.get("info")
    title = None
    if not isinstance(info, dict):
        add("#/info", "Missing required 'info' object.", "error", "missing-info")
    else:
        title = info.get("title")
        if not title:
            add("#/info/title", "Missing required 'info.title'.", "error", "missing-title")
        if not info.get("version"):
            add("#/info/version", "Missing required 'info.version'.", "warning", "missing-info-version")

    is_31 = isinstance(openapi_version, str) and openapi_version.startswith("3.1")
    paths = document.get("paths")
    webhooks = document.get("webhooks") if is_31 else None
    components = document.get("components")
    if not isinstance(paths, dict):
        paths = {}
    if not isinstance(webhooks, dict):
        webhooks = {}
    if not paths and not webhooks and not isinstance(components, dict):
        add(
            "#/paths",
            "Document must define at least one of 'paths', 'webhooks' or 'components'.",
            "error",
            "missing-paths",
        )

    operation_ids: dict[str, str] = {}

    sections: list[tuple[str, str, dict[str, Any]]] = [("paths", "#/paths", paths)]
    if is_31:
        sections.append(("webhooks", "#/webhooks", webhooks))

    for section, base_ref, mapping in sections:
        for path, path_item in mapping.items():
            path_ref = f"{base_ref}/{path}"
            if section == "paths" and not str(path).startswith("/") and not str(path).startswith("x-"):
                add(path_ref, f"Path '{path}' must start with '/'.", "error", "invalid-path")
            if not isinstance(path_item, dict):
                add(path_ref, "Path item must be an object.", "error", "invalid-path-item")
                continue

            for method, operation in path_item.items():
                if method.startswith("x-") or method == "parameters":
                    continue
                if method.lower() not in _HTTP_METHODS:
                    add(f"{path_ref}/{method}", f"Unknown HTTP method '{method}'.", "warning", "unknown-method")
                    continue
                op_ref = f"{path_ref}/{method}"
                if not isinstance(operation, dict):
                    add(op_ref, "Operation must be an object.", "error", "invalid-operation")
                    continue

                responses = operation.get("responses")
                if not isinstance(responses, dict) or not responses:
                    add(op_ref, "Operation is missing a 'responses' object.", "error", "missing-responses")
                else:
                    for status in responses:
                        if not (str(status).isdigit() or str(status) == "default"):
                            add(
                                f"{op_ref}/responses/{status}",
                                f"Invalid response status '{status}'.",
                                "warning",
                                "invalid-status",
                            )

                operation_id = operation.get("operationId")
                if isinstance(operation_id, str):
                    if operation_id in operation_ids:
                        add(
                            op_ref,
                            f"Duplicate operationId '{operation_id}' (also used at {operation_ids[operation_id]}).",
                            "warning",
                            "duplicate-operation-id",
                        )
                    else:
                        operation_ids[operation_id] = op_ref

                if section == "paths":
                    _check_path_parameters(path, path_item, operation, op_ref, add)

    for ref_path, ref in _iter_refs(document):
        if ref.startswith("#/"):
            if resolve_ref(document, ref) is None:
                add(ref_path, f"Unresolved $ref '{ref}'.", "error", "unresolved-ref")
        else:
            add(ref_path, f"External $ref '{ref}' is not supported by the validator.", "warning", "external-ref")

    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict):
            for name, schema in schemas.items():
                if not isinstance(schema, dict):
                    add(f"#/components/schemas/{name}", "Schema must be an object.", "error", "invalid-schema")
                    continue
                required = schema.get("required")
                properties = schema.get("properties")
                if isinstance(required, list) and isinstance(properties, dict):
                    for field_name in required:
                        if field_name not in properties:
                            add(
                                f"#/components/schemas/{name}",
                                f"Required field '{field_name}' is not declared in properties.",
                                "warning",
                                "required-not-declared",
                            )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    valid = error_count == 0

    summary = (
        "OpenAPI document is structurally valid."
        if valid
        else f"Found {error_count} error(s) and {warning_count} warning(s)."
    )

    return ValidationReport(
        valid=valid,
        issue_count=len(issues),
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        openapi_version=openapi_version if isinstance(openapi_version, str) else None,
        title=title if isinstance(title, str) else None,
        summary=summary,
    )


def _check_path_parameters(
    path: str,
    path_item: dict[str, Any],
    operation: dict[str, Any],
    op_ref: str,
    add,
) -> None:
    import re

    template_params = set(re.findall(r"\{([^{}]+)\}", str(path)))
    declared: set[str] = set()
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if not isinstance(source, list):
            continue
        for parameter in source:
            if isinstance(parameter, dict) and parameter.get("in") == "path":
                name = parameter.get("name")
                if name:
                    declared.add(str(name))
    missing = template_params - declared
    if missing:
        add(op_ref, f"Path parameters not declared: {sorted(missing)}", "error", "undeclared-path-param")
