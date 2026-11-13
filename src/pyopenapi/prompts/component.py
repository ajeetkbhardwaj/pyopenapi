"""Component/schema reasoning prompt."""

from __future__ import annotations

import json
from typing import Any

from ..ir.models import NormalizedComponent

COMPONENT_SYSTEM_PROMPT = (
    "You are a precise API schema editor. You receive a normalized component "
    "extracted from source code. Return one JSON object and no prose. Never add "
    "or remove fields, change types, or invent constraints. Only improve "
    "descriptions and examples that are supported by the field names and source "
    "evidence. Preserve every structural field."
)

COMPONENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["description"],
    "properties": {
        "description": {"type": "string"},
        "fields": {"type": "object"},
        "confidence": {"type": "number"},
    },
}


def build_component_prompt(component: NormalizedComponent) -> str:
    payload = {
        "task": "Improve component and field documentation text.",
        "response_shape": {
            "description": "component description",
            "fields": {"<field>": {"description": "text", "example": "value"}},
            "confidence": 0.0,
        },
        "component": component.model_dump(mode="json", exclude_none=True),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
