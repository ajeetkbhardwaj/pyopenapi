"""Static enrichment for Django serializer/model components.

The upstream analyser returns serializer components but leaves field extraction
to the LLM. For the deterministic ``static`` engine we reconstruct fields from
``ModelSerializer.Meta`` plus the referenced model's field declarations.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .ast_utils import ImportMap, ModuleResolver, dotted_name, kwarg, string_arg

_MODEL_FIELD_TYPES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"EmailField$"), {"type": "string", "format": "email"}),
    (re.compile(r"URLField$"), {"type": "string", "format": "uri"}),
    (re.compile(r"DateTimeField$"), {"type": "string", "format": "date-time"}),
    (re.compile(r"DateField$"), {"type": "string", "format": "date"}),
    (re.compile(r"TimeField$"), {"type": "string", "format": "time"}),
    (re.compile(r"UUIDField$"), {"type": "string", "format": "uuid"}),
    (re.compile(r"DecimalField$"), {"type": "number"}),
    (re.compile(r"FloatField$"), {"type": "number", "format": "float"}),
    (
        re.compile(r"(BigAutoField|AutoField|BigIntegerField|SmallIntegerField|IntegerField|PositiveIntegerField|PositiveSmallIntegerField)$"),
        {"type": "integer"},
    ),
    (re.compile(r"BooleanField$"), {"type": "boolean"}),
    (re.compile(r"(JSONField|HStoreField)$"), {"type": "object"}),
    (re.compile(r"ManyToManyField$"), {"type": "array", "items": {"type": "integer"}}),
    (re.compile(r"(ForeignKey|OneToOneField)$"), {"type": "integer"}),
    (re.compile(r"(FileField|ImageField)$"), {"type": "string", "format": "uri"}),
    (re.compile(r"(CharField|TextField|SlugField|ChoiceField|GenericIPAddressField)$"), {"type": "string"}),
)

_SERIALIZER_FIELD_TYPES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"EmailField$"), {"type": "string", "format": "email"}),
    (re.compile(r"URLField$"), {"type": "string", "format": "uri"}),
    (re.compile(r"DateTimeField$"), {"type": "string", "format": "date-time"}),
    (re.compile(r"DateField$"), {"type": "string", "format": "date"}),
    (re.compile(r"TimeField$"), {"type": "string", "format": "time"}),
    (re.compile(r"UUIDField$"), {"type": "string", "format": "uuid"}),
    (re.compile(r"(FloatField|DecimalField)$"), {"type": "number"}),
    (re.compile(r"(IntegerField|PrimaryKeyRelatedField)$"), {"type": "integer"}),
    (re.compile(r"BooleanField$"), {"type": "boolean"}),
    (re.compile(r"(JSONField|DictField)$"), {"type": "object"}),
    (re.compile(r"ListField$"), {"type": "array", "items": {"type": "object"}}),
    (re.compile(r"StringRelatedField$"), {"type": "string"}),
    (re.compile(r"SlugRelatedField$"), {"type": "string"}),
    (re.compile(r"SerializerMethodField$"), {"type": "object", "read_only": True}),
    (re.compile(r"(CharField|ChoiceField|FileField|ImageField)$"), {"type": "string"}),
)


def _class_node(resolver: ModuleResolver, path: str | Path, name: str) -> ast.ClassDef | None:
    return resolver.find_class(Path(path), name)


def _kwarg_bool(call: ast.Call, name: str) -> bool | None:
    node = kwarg(call, name)
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _kwarg_int(call: ast.Call, name: str) -> int | None:
    node = kwarg(call, name)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _model_field_schema(field_name: str, call: ast.Call) -> dict[str, Any]:
    field_class = (dotted_name(call.func) or "").split(".")[-1]
    schema: dict[str, Any] = {"name": field_name}
    for pattern, mapping in _MODEL_FIELD_TYPES:
        if pattern.search(field_class):
            schema.update(mapping)
            break
    else:
        schema["type"] = "string"

    null = _kwarg_bool(call, "null")
    blank = _kwarg_bool(call, "blank")
    primary_key = _kwarg_bool(call, "primary_key")
    editable = _kwarg_bool(call, "editable")
    auto = bool(_kwarg_bool(call, "auto_now") or _kwarg_bool(call, "auto_now_add"))
    has_default = kwarg(call, "default") is not None

    schema["required"] = not bool(null or blank or primary_key or auto or has_default)
    if primary_key or auto or editable is False:
        schema["read_only"] = True
    max_length = _kwarg_int(call, "max_length")
    if max_length:
        schema["max_length"] = max_length
    return schema


def _parse_model_fields(class_node: ast.ClassDef) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                fields[target.id] = _model_field_schema(target.id, node.value)
    return fields


def _parse_serializer_overrides(class_node: ast.ClassDef) -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            call = node.value
            field_class = (dotted_name(call.func) or "").split(".")[-1]
            schema: dict[str, Any] = {"name": target.id}
            for pattern, mapping in _SERIALIZER_FIELD_TYPES:
                if pattern.search(field_class):
                    schema.update(mapping)
                    break
            else:
                if field_class.endswith("Serializer"):
                    schema["ref"] = field_class
                    schema["type"] = "object"
                else:
                    schema["type"] = "string"

            required = _kwarg_bool(call, "required")
            if required is not None:
                schema["required"] = required
            read_only = _kwarg_bool(call, "read_only")
            if read_only is not None:
                schema["read_only"] = read_only
            write_only = _kwarg_bool(call, "write_only")
            if write_only is not None:
                schema["write_only"] = write_only
            allow_null = _kwarg_bool(call, "allow_null")
            if allow_null:
                schema["nullable"] = True
            max_length = _kwarg_int(call, "max_length")
            if max_length:
                schema["max_length"] = max_length
            overrides[target.id] = schema
    return overrides


def _parse_meta(class_node: ast.ClassDef, resolver: ModuleResolver, file_path: Path) -> tuple[ast.ClassDef | None, list[str] | None, set[str]]:
    model_node: ast.ClassDef | None = None
    meta_fields: list[str] | None = None
    exclude: set[str] = set()

    for node in class_node.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Meta":
            continue
        imports = ImportMap.from_tree(resolver.tree(file_path) or ast.Module(body=[], type_ignores=[]))
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            targets = [target.id for target in item.targets if isinstance(target, ast.Name)]
            if "model" in targets:
                resolved = imports.resolve_name(item.value)
                if resolved and resolved[0]:
                    module_path = resolver.resolve_module(resolved[0], from_file=file_path)
                    symbol = resolved[1]
                    if module_path and symbol:
                        model_node = resolver.find_class(module_path, symbol)
            elif "fields" in targets:
                if isinstance(item.value, ast.Constant) and item.value.value == "__all__":
                    meta_fields = ["__all__"]
                elif isinstance(item.value, (ast.List, ast.Tuple)):
                    meta_fields = [string_arg(element) for element in item.value.elts if string_arg(element)]
            elif "exclude" in targets and isinstance(item.value, (ast.List, ast.Tuple)):
                exclude = {string_arg(element) for element in item.value.elts if string_arg(element)}
    return model_node, meta_fields, exclude


def enrich_django_components(
    project_path: Path,
    components: dict[str, dict[str, Any]],
    *,
    max_files: int = 20_000,
    max_bytes: int = 2_000_000,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    warnings: list[str] = []
    resolver = ModuleResolver(project_path, max_files=max_files, max_bytes=max_bytes)
    enriched: dict[str, dict[str, Any]] = dict(components)

    for name, raw in list(components.items()):
        if not isinstance(raw, dict) or raw.get("type") != "serializer":
            continue
        path = raw.get("path")
        if not path:
            continue
        class_node = _class_node(resolver, path, name)
        if class_node is None:
            continue

        model_node, meta_fields, exclude = _parse_meta(class_node, resolver, Path(path))
        model_fields = _parse_model_fields(model_node) if model_node else {}
        overrides = _parse_serializer_overrides(class_node)

        selected: list[str] = []
        if meta_fields and meta_fields != ["__all__"]:
            selected.extend(meta_fields)
        else:
            selected.extend(model_fields.keys())
        for extra in overrides:
            if extra not in selected:
                selected.append(extra)

        fields: list[dict[str, Any]] = []
        for field_name in selected:
            if field_name in exclude:
                continue
            if field_name in model_fields:
                base = dict(model_fields[field_name])
            elif field_name in overrides:
                base = dict(overrides[field_name])
            elif field_name == "id" or field_name.endswith("_id"):
                base = {"name": field_name, "type": "integer", "read_only": True}
            else:
                base = {"name": field_name, "type": "string"}
            base.update(overrides.get(field_name) or {})
            base["name"] = field_name
            fields.append(base)

        if fields:
            raw = dict(raw)
            raw["properties"] = fields
            raw["supports_request"] = True
            raw["supports_response"] = True
            enriched[name] = raw

        if model_node is not None and model_node.name not in enriched:
            model_fields_payload = list(_parse_model_fields(model_node).values())
            enriched[model_node.name] = {
                "name": model_node.name,
                "type": "model",
                "path": str(path),
                "parent_classes": [],
                "properties": model_fields_payload,
                "data_classes": [],
                "supports_request": False,
                "supports_response": True,
            }

    return enriched, warnings
