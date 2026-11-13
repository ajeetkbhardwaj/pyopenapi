"""Deterministic OpenAPI schema construction for the agent assembler.

These helpers are intentionally shared static tools, not a fallback spec
builder. They preserve structural facts extracted from source (types, refs,
required flags, enums, constraints) while agents only add human-facing text.
The field mapping mirrors the proven static analyzer behavior so agent output
does not lose accuracy on type and nullability handling.
"""

from __future__ import annotations

import re
from typing import Any

from ..ir.models import NormalizedComponent, NormalizedField


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def apply_nullable(schema: dict[str, Any], openapi_version: str) -> dict[str, Any]:
    if not schema.pop("nullable", False):
        return schema
    if not openapi_version.startswith("3.1"):
        schema["nullable"] = True
        return schema

    type_name = schema.get("type")
    if isinstance(type_name, str):
        schema["type"] = [type_name, "null"]
    elif isinstance(type_name, list):
        if "null" not in type_name:
            schema["type"] = [*type_name, "null"]
    elif "$ref" in schema:
        reference = schema.pop("$ref")
        schema["anyOf"] = [{"$ref": reference}, {"type": "null"}]
    else:
        schema["type"] = ["object", "null"]
    return schema


_DJANGO_FIELD_TYPES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"EmailField|EmailStr", re.IGNORECASE), {"type": "string", "format": "email"}),
    (re.compile(r"URLField|AnyUrl|HttpUrl", re.IGNORECASE), {"type": "string", "format": "uri"}),
    (re.compile(r"DateTimeField", re.IGNORECASE), {"type": "string", "format": "date-time"}),
    (re.compile(r"DateField", re.IGNORECASE), {"type": "string", "format": "date"}),
    (re.compile(r"TimeField", re.IGNORECASE), {"type": "string", "format": "time"}),
    (re.compile(r"UUIDField|UUID", re.IGNORECASE), {"type": "string", "format": "uuid"}),
    (re.compile(r"DecimalField|FloatField|Decimal", re.IGNORECASE), {"type": "number"}),
    (
        re.compile(
            r"IntegerField|BigIntegerField|SmallIntegerField|PositiveIntegerField|AutoField",
            re.IGNORECASE,
        ),
        {"type": "integer"},
    ),
    (re.compile(r"BooleanField|Boolean", re.IGNORECASE), {"type": "boolean"}),
    (re.compile(r"JSONField|DictField|HStoreField", re.IGNORECASE), {"type": "object"}),
    (
        re.compile(r"ManyToMany|ListField|ManyRelatedField", re.IGNORECASE),
        {"type": "array", "items": {"type": "object"}},
    ),
    (
        re.compile(r"CharField|TextField|SlugField|FilePathField|ChoiceField|EmailField", re.IGNORECASE),
        {"type": "string"},
    ),
)


def schema_from_python_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        return {"type": "string"}

    serializer_match = re.search(r"\b([A-Za-z_][\w]*Serializer)\s*\(", cleaned)
    if serializer_match:
        return ref(serializer_match.group(1))

    lowered = cleaned.lower()
    if lowered in {"str", "string"}:
        return {"type": "string"}
    if lowered in {"int", "integer"}:
        return {"type": "integer"}
    if lowered in {"float", "number", "decimal"}:
        return {"type": "number"}
    if lowered in {"bool", "boolean"}:
        return {"type": "boolean"}
    if lowered.startswith(("list[", "list(")) or "many=true" in lowered:
        return {"type": "array", "items": {"type": "object"}}
    if lowered.startswith("optional[") and cleaned.endswith("]"):
        return schema_from_python_text(cleaned[len("Optional[") : -1])

    for pattern, schema in _DJANGO_FIELD_TYPES:
        if pattern.search(cleaned):
            return dict(schema)

    return {"type": "string"}


def schema_from_field(field: NormalizedField, *, openapi_version: str = "3.0.3") -> dict[str, Any]:
    if field.ref:
        reference = ref(str(field.ref))
        if field.nullable:
            if openapi_version.startswith("3.1"):
                return {"anyOf": [reference, {"type": "null"}]}
            return {"allOf": [reference], "nullable": True}
        return reference

    if field.type == "array":
        items = field.items or {"type": "object"}
        schema: dict[str, Any] = {
            "type": "array",
            "items": items if isinstance(items, dict) else {"type": "object"},
        }
    elif field.type in {"object", "string", "integer", "number", "boolean"}:
        schema = {"type": field.type}
    else:
        schema = schema_from_python_text(str(field.type or ""))

    if field.format:
        schema["format"] = field.format
    if field.description:
        schema["description"] = field.description
    if field.enum:
        schema["enum"] = field.enum
    if field.max_length:
        schema["maxLength"] = field.max_length
    if field.nullable:
        schema = apply_nullable({**schema, "nullable": True}, openapi_version)
    if field.read_only:
        schema["readOnly"] = True
    if field.write_only:
        schema["writeOnly"] = True
    if field.default is not None and isinstance(field.default, (str, int, float, bool)):
        schema["default"] = field.default
    return schema


def component_schema(
    component: NormalizedComponent,
    *,
    openapi_version: str = "3.0.3",
    description: str | None = None,
) -> dict[str, Any]:
    if component.kind.lower() == "enum":
        values: list[Any] = []
        for field in component.fields:
            value: Any = field.metadata.get(
                "value", field.default if field.default is not None else field.name
            )
            if value is not None:
                values.append(value)

        def value_type(value: Any) -> str | None:
            if isinstance(value, bool):
                return "boolean"
            if isinstance(value, int):
                return "integer"
            if isinstance(value, float):
                return "number"
            if isinstance(value, str):
                return "string"
            return None

        detected = {value_type(value) for value in values}
        detected.discard(None)
        schema_type = detected.pop() if len(detected) == 1 else "string"
        schema: dict[str, Any] = {"type": schema_type, "enum": values}
        if description:
            schema["description"] = description
        return schema

    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in component.fields:
        properties[field.name] = schema_from_field(field, openapi_version=openapi_version)
        if field.required:
            required.append(field.name)

    schema = {"type": "object"}
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema
