"""Endpoint reasoning prompt."""

from __future__ import annotations

import json
from typing import Any

from ..ir.models import NormalizedEndpoint

ENDPOINT_SYSTEM_PROMPT = (
    "You are a precise API specification editor. You receive a normalized "
    "endpoint extracted from source code. Return one JSON object and no prose. "
    "Never invent paths, methods, parameters, schemas or response codes. Only "
    "improve human-facing text: summaries, descriptions, parameter descriptions, "
    "request/response descriptions and tags. Preserve every structural field. "
    "If evidence is insufficient, keep text generic and lower confidence."
)

ENDPOINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["operations"],
    "properties": {
        "operations": {"type": "object"},
        "confidence": {"type": "number"},
    },
}


def build_endpoint_prompt(endpoint: NormalizedEndpoint) -> str:
    payload = {
        "task": "Improve endpoint documentation text.",
        "response_shape": {
            "operations": {
                "<method>": {
                    "summary": "short summary",
                    "description": "1-3 sentences",
                    "tags": ["tag"],
                    "parameters": {"<name>": {"description": "text"}},
                    "request_description": "text",
                    "responses": {"<status>": {"description": "text"}},
                }
            },
            "confidence": 0.0,
        },
        "endpoint": endpoint.model_dump(mode="json", exclude_none=True),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
