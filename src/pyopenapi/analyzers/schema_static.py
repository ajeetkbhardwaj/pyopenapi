"""Static schema (component) extraction for Flask and FastAPI projects.

Supports the two dominant modelling styles:

* Pydantic ``BaseModel`` classes (FastAPI, and increasingly Flask)
* Marshmallow ``Schema`` classes (Flask / flask-smorest)

Extraction is best-effort and dependency-free; it is intended to give agents a
useful starting point that the LLM pipeline can enrich later.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .ast_utils import (
    ModuleResolver,
    class_bases,
    dotted_name,
    kwarg,
    kwarg_string,
    string_arg,
)

_PRIMITIVES: dict[str, tuple[str, str | None]] = {
    "str": ("string", None),
    "int": ("integer", None),
    "float": ("number", "float"),
    "bool": ("boolean", None),
    "bytes": ("string", "byte"),
    "Any": ("object", None),
    "datetime": ("string", "date-time"),
    "date": ("string", "date"),
    "time": ("string", "time"),
    "UUID": ("string", "uuid"),
    "Decimal": ("number", "decimal"),
    "EmailStr": ("string", "email"),
    "AnyUrl": ("string", "uri"),
    "HttpUrl": ("string", "uri"),
    "SecretStr": ("string", "password"),
    "Json": ("object", None),
    "dict": ("object", None),
    "Dict": ("object", None),
}

_SEQUENCE_NAMES = {"list", "List", "set", "Set", "Sequence", "Iterable", "Tuple", "tuple"}
_MAPPING_NAMES = {"dict", "Dict", "Mapping", "MutableMapping"}
_OPTIONAL_NAMES = {"Optional", "Union"}

_MARSHMALLOW_TYPES: dict[str, tuple[str, str | None]] = {
    "Str": ("string", None),
    "String": ("string", None),
    "Integer": ("integer", None),
    "Int": ("integer", None),
    "Float": ("number", "float"),
    "Number": ("number", None),
    "Boolean": ("boolean", None),
    "Bool": ("boolean", None),
    "DateTime": ("string", "date-time"),
    "Date": ("string", "date"),
    "Time": ("string", "time"),
    "UUID": ("string", "uuid"),
    "Email": ("string", "email"),
    "URL": ("string", "uri"),
    "Url": ("string", "uri"),
    "Decimal": ("number", "decimal"),
    "List": ("array", None),
    "Tuple": ("array", None),
    "Raw": ("object", None),
    "Dict": ("object", None),
}


def _annotation_type(node: ast.AST | None, model_names: set[str]) -> dict[str, Any]:
    """Translate a Python annotation into a compact schema descriptor."""

    if node is None:
        return {"type": "object"}

    if isinstance(node, ast.Constant):
        if node.value is None:
            return {"type": "object", "nullable": True}
        return {"type": "object"}

    if isinstance(node, ast.Name):
        name = node.id
        if name in _PRIMITIVES:
            type_name, fmt = _PRIMITIVES[name]
            return {"type": type_name, **({"format": fmt} if fmt else {})}
        if name in model_names:
            return {"type": "object", "ref": name}
        return {"type": "object", "python_type": name}

    if isinstance(node, ast.Attribute):
        return _annotation_type(ast.Name(id=node.attr, ctx=ast.Load()), model_names)

    if isinstance(node, ast.Subscript):
        base = dotted_name(node.value) or ""
        base = base.split(".")[-1]
        inner = node.slice
        inner_nodes = inner.elts if isinstance(inner, ast.Tuple) else [inner]

        if base in _OPTIONAL_NAMES:
            non_none = [
                item
                for item in inner_nodes
                if not (isinstance(item, ast.Constant) and item.value is None)
            ]
            if len(non_none) == 1:
                descriptor = _annotation_type(non_none[0], model_names)
                descriptor["optional"] = True
                descriptor["nullable"] = True
                return descriptor
            return {"type": "object", "optional": True}

        if base in _SEQUENCE_NAMES:
            items = _annotation_type(inner_nodes[0], model_names) if inner_nodes else {"type": "object"}
            return {"type": "array", "items": items}

        if base in _MAPPING_NAMES:
            value_node = inner_nodes[-1] if inner_nodes else None
            return {
                "type": "object",
                "additionalProperties": _annotation_type(value_node, model_names),
            }

        return {"type": "object", "python_type": ast.unparse(node)}

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _annotation_type(node.left, model_names)
        right_is_none = isinstance(node.right, ast.Constant) and node.right.value is None
        if right_is_none:
            left["optional"] = True
            left["nullable"] = True
        return left

    try:
        return {"type": "object", "python_type": ast.unparse(node)}
    except Exception:
        return {"type": "object"}


def _field_call_kwargs(call: ast.Call) -> dict[str, Any]:
    info: dict[str, Any] = {}
    description = kwarg_string(call, "description")
    if description:
        info["description"] = description
    title = kwarg_string(call, "title")
    if title:
        info["title"] = title
    alias = kwarg_string(call, "alias")
    if alias:
        info["alias"] = alias
    metadata = kwarg(call, "metadata")
    if isinstance(metadata, ast.Dict):
        for key_node, value_node in zip(metadata.keys, metadata.values, strict=False):
            key = string_arg(key_node)
            if key == "description":
                text = string_arg(value_node)
                if text:
                    info["description"] = text
    return info


def _is_field_call(value: ast.AST | None) -> ast.Call | None:
    if isinstance(value, ast.Call):
        name = (dotted_name(value.func) or "").split(".")[-1]
        if name == "Field":
            return value
    return None


def _extract_pydantic_fields(
    class_node: ast.ClassDef, model_names: set[str]
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for node in class_node.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        name = node.target.id
        descriptor = _annotation_type(node.annotation, model_names)
        default = node.value
        required = default is None and not descriptor.get("optional")
        field_info: dict[str, Any] = {
            "name": name,
            "type": descriptor.get("type"),
            "required": required,
        }
        if descriptor.get("format"):
            field_info["format"] = descriptor["format"]
        if descriptor.get("ref"):
            field_info["ref"] = descriptor["ref"]
        if descriptor.get("items"):
            field_info["items"] = descriptor["items"]
        if descriptor.get("nullable"):
            field_info["nullable"] = True

        field_call = _is_field_call(default)
        if field_call is not None:
            field_info.update(_field_call_kwargs(field_call))
            if field_call.args and not (
                isinstance(field_call.args[0], ast.Constant)
                and field_call.args[0].value is Ellipsis
            ):
                required = False
            default_kw = kwarg(field_call, "default")
            if default_kw is not None:
                required = False
        elif isinstance(default, ast.Constant):
            required = False
            if default.value is Ellipsis:
                required = True

        field_info["required"] = required
        fields.append(field_info)
    return fields


def _extract_marshmallow_fields(class_node: ast.ClassDef) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not targets:
            continue
        call = node.value
        field_type = (dotted_name(call.func) or "").split(".")[-1]
        type_name, fmt = _MARSHMALLOW_TYPES.get(field_type, ("object", None))
        for name in targets:
            field_info: dict[str, Any] = {"name": name, "type": type_name, "required": False}
            if fmt:
                field_info["format"] = fmt
            required_kw = kwarg(call, "required")
            if isinstance(required_kw, ast.Constant) and isinstance(required_kw.value, bool):
                field_info["required"] = required_kw.value
            if field_type == "Nested" and call.args:
                nested = dotted_name(call.args[0])
                if nested:
                    field_info["ref"] = nested.split(".")[-1]
            if field_type in {"List", "Tuple"} and call.args:
                inner = dotted_name(call.args[0])
                if inner:
                    inner_type, inner_fmt = _MARSHMALLOW_TYPES.get(inner.split(".")[-1], ("object", None))
                    field_info["items"] = {
                        "type": inner_type,
                        **({"format": inner_fmt} if inner_fmt else {}),
                    }
            dump_only = kwarg(call, "dump_only")
            load_only = kwarg(call, "load_only")
            if isinstance(dump_only, ast.Constant) and dump_only.value is True:
                field_info["read_only"] = True
            if isinstance(load_only, ast.Constant) and load_only.value is True:
                field_info["write_only"] = True
            field_info.update(_field_call_kwargs(call))
            fields.append(field_info)
    return fields


def _extract_enum_values(class_node: ast.ClassDef) -> list[str]:
    values: list[str] = []
    for node in class_node.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            values.append(node.target.id)
    return values


def collect_components(
    project_path: Path,
    resolver: ModuleResolver,
    *,
    include_pydantic: bool = True,
    include_marshmallow: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Collect Pydantic/Marshmallow/Enum components across the project."""

    warnings: list[str] = []
    classes: list[tuple[str, Path, ast.ClassDef]] = []

    for path in resolver.index.values():
        tree = resolver.tree(path)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append((node.name, path, node))

    model_names = {name for name, _, _ in classes}

    components: dict[str, dict[str, Any]] = {}
    for name, path, node in classes:
        bases = class_bases(node)
        base_text = " ".join(bases)
        if include_pydantic and "BaseModel" in bases:
            fields = _extract_pydantic_fields(node, model_names)
            components[name] = {
                "name": name,
                "type": "pydantic_model",
                "path": str(path),
                "parent_classes": bases,
                "properties": fields,
                "data_classes": [
                    {"name": field["ref"], "type": "model"}
                    for field in fields
                    if field.get("ref")
                ],
                "supports_request": True,
                "supports_response": True,
            }
        elif include_marshmallow and ("Schema" in base_text or "Schema" in name):
            fields = _extract_marshmallow_fields(node)
            components[name] = {
                "name": name,
                "type": "marshmallow_schema",
                "path": str(path),
                "parent_classes": bases,
                "properties": fields,
                "data_classes": [
                    {"name": field["ref"], "type": "schema"}
                    for field in fields
                    if field.get("ref")
                ],
                "supports_request": True,
                "supports_response": True,
            }
        elif "Enum" in bases or "StrEnum" in bases or "IntEnum" in bases:
            components[name] = {
                "name": name,
                "type": "enum",
                "path": str(path),
                "parent_classes": bases,
                "properties": [
                    {"name": value, "type": "string", "required": False}
                    for value in _extract_enum_values(node)
                ],
                "data_classes": [],
                "supports_request": True,
                "supports_response": True,
            }

    if not components:
        warnings.append(
            "No Pydantic models, Marshmallow schemas or Enums were detected."
        )
    return components, warnings
