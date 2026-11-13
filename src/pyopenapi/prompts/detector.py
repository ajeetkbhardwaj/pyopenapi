"""Project metadata/detector prompt."""

from __future__ import annotations

import json
from typing import Any

from ..ir.models import NormalizedProject

DETECTOR_SYSTEM_PROMPT = (
    "You are a precise API metadata editor. You receive a normalized project "
    "summary. Return one JSON object and no prose. Infer only what the evidence "
    "supports. Never invent endpoints or schemas. Keep the title short and the "
    "description factual."
)

DETECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "tags": {"type": "array"},
        "confidence": {"type": "number"},
    },
}


def build_detector_prompt(project: NormalizedProject) -> str:
    payload = {
        "task": "Infer API title, description and top-level tags.",
        "response_shape": {
            "title": "short API title",
            "description": "1-3 sentences",
            "version": "0.1.0",
            "tags": ["tag"],
            "confidence": 0.0,
        },
        "project": {
            "framework": project.framework,
            "project_path": project.project_path,
            "endpoints": [
                {
                    "path": endpoint.path,
                    "methods": [operation.method for operation in endpoint.operations],
                    "tags": endpoint.tags,
                }
                for endpoint in project.endpoints
            ],
            "components": [
                {"name": component.name, "kind": component.kind}
                for component in project.components.values()
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
