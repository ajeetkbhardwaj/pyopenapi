"""Deterministic quality checks for assembled agent output."""

from __future__ import annotations

from typing import Any

from ..ir.models import NormalizedProject
from .tracing import QualityFinding

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def audit_spec(spec: dict[str, Any], project: NormalizedProject) -> tuple[list[QualityFinding], float]:
    findings: list[QualityFinding] = []
    checks = 0
    passed = 0

    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            label = f"{method.upper()} {path}"
            for key in ("summary", "description"):
                checks += 1
                if operation.get(key):
                    passed += 1
                else:
                    findings.append(
                        QualityFinding(
                            code="operation_missing_text",
                            message=f"{label} has no {key}.",
                            severity="warning",
                            category="documentation",
                            stage="quality",
                            item_id=operation.get("operationId"),
                            metadata={"path": path, "method": method, "field": key},
                        )
                    )
            for parameter in operation.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                checks += 1
                if parameter.get("description"):
                    passed += 1
                else:
                    findings.append(
                        QualityFinding(
                            code="parameter_missing_description",
                            message=f"{label} parameter '{parameter.get('name')}' has no description.",
                            severity="info",
                            category="documentation",
                            stage="quality",
                            item_id=operation.get("operationId"),
                            metadata={"path": path, "method": method, "parameter": parameter.get("name")},
                        )
                    )
            responses = operation.get("responses")
            if isinstance(responses, dict):
                for status, response in responses.items():
                    if not isinstance(response, dict):
                        continue
                    checks += 1
                    if response.get("description"):
                        passed += 1
                    else:
                        findings.append(
                            QualityFinding(
                                code="response_missing_description",
                                message=f"{label} response {status} has no description.",
                                severity="info",
                                category="documentation",
                                stage="quality",
                                item_id=operation.get("operationId"),
                                metadata={"path": path, "method": method, "status": status},
                            )
                        )

    for name, schema in ((spec.get("components") or {}).get("schemas") or {}).items():
        if not isinstance(schema, dict):
            continue
        checks += 1
        if schema.get("description"):
            passed += 1
        else:
            findings.append(
                QualityFinding(
                    code="component_missing_description",
                    message=f"Component '{name}' has no description.",
                    severity="info",
                    category="documentation",
                    stage="quality",
                    item_id=name,
                    metadata={"component": name},
                )
            )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for field_name, field_schema in properties.items():
                if not isinstance(field_schema, dict):
                    continue
                checks += 1
                if field_schema.get("description"):
                    passed += 1
                else:
                    findings.append(
                        QualityFinding(
                            code="field_missing_description",
                            message=f"Component '{name}' field '{field_name}' has no description.",
                            severity="info",
                            category="documentation",
                            stage="quality",
                            item_id=name,
                            metadata={"component": name, "field": field_name},
                        )
                    )

    score = (passed / checks) if checks else 1.0
    return findings, round(score, 3)
