"""Quality-audit prompt."""

from __future__ import annotations

import json
from typing import Any

from ..ir.models import NormalizedProject

QUALITY_SYSTEM_PROMPT = (
    "You are an API documentation quality auditor. You receive a normalized "
    "project summary. Return one JSON object and no prose. Report only concrete, "
    "evidence-based gaps such as missing descriptions, examples, error responses "
    "or auth hints. Never invent behavior. Prefer a small number of high-value "
    "findings over generic advice."
)

QUALITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {"type": "array"},
        "score": {"type": "number"},
    },
}


def build_quality_prompt(project: NormalizedProject) -> str:
    payload = {
        "task": "Audit documentation completeness and agent readiness.",
        "response_shape": {
            "findings": [
                {
                    "code": "missing.description",
                    "message": "text",
                    "severity": "info|warning|error",
                    "item_id": "endpoint/component id",
                }
            ],
            "score": 0.0,
        },
        "project": {
            "framework": project.framework,
            "endpoints": [
                {
                    "id": endpoint.endpoint_id,
                    "path": endpoint.path,
                    "operations": [
                        {
                            "method": operation.method,
                            "summary": operation.summary,
                            "description": operation.description,
                            "tags": operation.tags,
                            "parameters": [item.name for item in operation.parameters],
                            "responses": [item.status for item in operation.responses],
                        }
                        for operation in endpoint.operations
                    ],
                }
                for endpoint in project.endpoints
            ],
            "components": [
                {
                    "id": component.component_id,
                    "name": component.name,
                    "kind": component.kind,
                    "field_count": len(component.fields),
                }
                for component in project.components.values()
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
