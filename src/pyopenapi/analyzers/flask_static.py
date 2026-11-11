"""Dependency-free static Flask endpoint extraction (preview).

Covers the common routing styles:

* ``@app.route`` / ``@bp.route`` and the shortcut verbs (``@app.get`` etc.)
* ``Blueprint(url_prefix=...)`` and ``register_blueprint(url_prefix=...)``
* ``add_url_rule`` (including ``MethodView.as_view()``)
* ``flask_restful.Api.add_resource`` with ``Resource`` classes
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ast_utils import (
    ImportMap,
    ModuleResolver,
    decorator_name,
    dotted_name,
    iter_class_methods,
    kwarg,
    kwarg_list_strings,
    kwarg_string,
    string_arg,
)
from .base import RawAnalysis
from .schema_static import collect_components

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_FLASK_METHOD_DECORATORS = {"route", "get", "post", "put", "patch", "delete"}
_CONVERTER_TYPES = {"int": "integer", "float": "number", "path": "string", "string": "string", "uuid": "string"}


@dataclass
class _BlueprintInfo:
    prefix: str = ""
    registration_prefix: str = ""


def _normalize_route(prefix: str, route: str) -> tuple[str, dict[str, str]]:
    converters: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        converter = match.group("converter")
        name = match.group("name")
        if converter:
            converters[name] = _CONVERTER_TYPES.get(converter, "string")
        return "{" + name + "}"

    route = re.sub(
        r"<(?:(?P<converter>[a-zA-Z_][\w]*):)?(?P<name>[a-zA-Z_][\w]*)>",
        _replace,
        route,
    )
    combined = "/" + "/".join(part.strip("/") for part in (prefix, route) if part and part.strip("/"))
    combined = re.sub(r"/{2,}", "/", combined) or "/"
    return combined, converters


def _parameters(path: str, converters: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "location": "path",
            "required": True,
            "type": converters.get(name, "string"),
        }
        for name in re.findall(r"\{([^{}]+)\}", path)
    ]


def _collect_local_definitions(tree: ast.Module) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return ``(apps, blueprints, registrations)`` defined in this module."""
    apps: dict[str, str] = {}
    blueprints: dict[str, str] = {}
    registrations: dict[str, str] = {}

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        ctor = (dotted_name(value.func) or "").split(".")[-1]
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if ctor == "Flask":
            for name in names:
                apps[name] = ""
        elif ctor == "Blueprint":
            prefix = kwarg_string(value, "url_prefix") or ""
            for name in names:
                blueprints[name] = prefix
        elif ctor == "Api":
            prefix = kwarg_string(value, "prefix") or ""
            for name in names:
                apps[name] = prefix
        elif ctor == "Namespace":
            prefix = kwarg_string(value, "path") or ""
            for name in names:
                blueprints[name] = prefix

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"register_blueprint", "add_namespace"}:
            continue
        if not node.args:
            continue
        blueprint = dotted_name(node.args[0])
        if not blueprint:
            continue
        prefix_key = "url_prefix" if node.func.attr == "register_blueprint" else "path"
        registrations[blueprint.split(".")[-1]] = kwarg_string(node, prefix_key) or ""

    return apps, blueprints, registrations


def _expr_name(expr: ast.AST | None) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Subscript):
        base = dotted_name(expr.value)
        return base.split(".")[-1] if base else None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _decorator_schemas(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    component_names: set[str],
) -> tuple[str | None, str | None]:
    """Extract request/response schemas from flask-smorest / flask-restx decorators."""

    request_schema: str | None = None
    response_schema: str | None = None
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        name = (decorator_name(decorator) or "").split(".")[-1]
        if name in {"arguments", "expect"}:
            schema_expr = kwarg(decorator, "schema")
            if schema_expr is None and decorator.args:
                schema_expr = decorator.args[0]
            candidate = _expr_name(schema_expr)
            if candidate in component_names:
                request_schema = request_schema or candidate
        elif name in {"response", "marshal_with", "produces"}:
            schema_expr = kwarg(decorator, "schema")
            if schema_expr is None:
                positional = [
                    arg for arg in decorator.args if not (isinstance(arg, ast.Constant) and isinstance(arg.value, int))
                ]
                schema_expr = positional[0] if positional else None
            candidate = _expr_name(schema_expr)
            if candidate in component_names:
                response_schema = response_schema or candidate
    return request_schema, response_schema


def _resolve_blueprint(imports: ImportMap, var: str) -> tuple[str | None, str | None]:
    """Resolve a variable to its blueprint/app definition.
    Returns (module_name, symbol) if found in imports, else (None, None)."""
    resolved = imports.resolve_name(ast.Name(id=var, ctx=ast.Load()))
    if resolved:
        return resolved
    return (None, None)


def _prefix_for_variable(
    variable: str,
    *,
    global_apps: dict[str, str],
    global_blueprints: dict[str, _BlueprintInfo],
    imports: ImportMap,
) -> str | None:
    if variable in global_apps:
        return global_apps[variable]
    if variable in global_blueprints:
        bp = global_blueprints[variable]
        return bp.prefix + bp.registration_prefix
    mod_name, symbol = _resolve_blueprint(imports, variable)
    if mod_name and symbol:
        if symbol in global_apps:
            return global_apps[symbol]
        if symbol in global_blueprints:
            bp = global_blueprints[symbol]
            return bp.prefix + bp.registration_prefix
    return None


def _endpoints_from_class(
    node: ast.ClassDef,
    *,
    global_apps: dict[str, str],
    global_blueprints: dict[str, _BlueprintInfo],
    imports: ImportMap,
    file_path: Path,
    component_names: set[str],
) -> list[dict[str, Any]]:
    """Handle class-based routes (flask-restx ``@ns.route`` on a Resource)."""

    endpoints: list[dict[str, Any]] = []
    for decorator in node.decorator_list:
        name = (decorator_name(decorator) or "")
        parts = name.split(".")
        if len(parts) < 2 or parts[-1] != "route":
            continue
        prefix = _prefix_for_variable(
            parts[-2],
            global_apps=global_apps,
            global_blueprints=global_blueprints,
            imports=imports,
        )
        if prefix is None:
            continue
        call = decorator if isinstance(decorator, ast.Call) else None
        if call is None or not call.args:
            continue
        route = string_arg(call.args[0])
        if route is None:
            continue
        path, converters = _normalize_route(prefix, route)
        for method_node in iter_class_methods(node):
            method = method_node.name.lower()
            if method not in _HTTP_METHODS:
                continue
            request_schema, response_schema = _decorator_schemas(method_node, component_names)
            endpoint = {
                "url": {"url": path, "parameter": _parameters(path, converters)},
                "method": method.upper(),
                "view": node.name,
                "function": method,
                "is_viewset": True,
                "path": str(file_path),
            }
            if request_schema:
                endpoint["request_serializer"] = request_schema
            if response_schema:
                endpoint["response_serializer"] = response_schema
            endpoints.append(endpoint)
    return endpoints


def _endpoint_from_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    global_apps: dict[str, str],
    global_blueprints: dict[str, _BlueprintInfo],
    imports: ImportMap,
    file_path: Path,
    component_names: set[str],
) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    request_schema, response_schema = _decorator_schemas(node, component_names)
    for decorator in node.decorator_list:
        name = (decorator_name(decorator) or "")
        parts = name.split(".")
        if len(parts) < 2:
            continue
        variable, verb = parts[-2], parts[-1]
        if verb not in _FLASK_METHOD_DECORATORS:
            continue

        # Check if variable is a locally defined app/blueprint
        if variable in global_apps:
            prefix = global_apps[variable]
        elif variable in global_blueprints:
            bp = global_blueprints[variable]
            prefix = bp.prefix + bp.registration_prefix
        else:
            # Try to resolve via imports
            mod_name, symbol = _resolve_blueprint(imports, variable)
            if mod_name and symbol:
                if symbol in global_apps:
                    prefix = global_apps[symbol]
                elif symbol in global_blueprints:
                    bp = global_blueprints[symbol]
                    prefix = bp.prefix + bp.registration_prefix
                else:
                    continue
            else:
                continue

        call = decorator if isinstance(decorator, ast.Call) else None
        if call is None or not call.args:
            continue
        route = string_arg(call.args[0])
        if route is None:
            continue

        path, converters = _normalize_route(prefix, route)
        if verb == "route":
            methods = kwarg_list_strings(call, "methods") or ["GET"]
        else:
            methods = [verb.upper()]
        for method in methods:
            endpoint = {
                "url": {"url": path, "parameter": _parameters(path, converters)},
                "method": method.upper(),
                "view": node.name,
                "function": node.name,
                "is_viewset": False,
                "path": str(file_path),
            }
            if request_schema:
                endpoint["request_serializer"] = request_schema
            if response_schema:
                endpoint["response_serializer"] = response_schema
            endpoints.append(endpoint)
    return endpoints


def _methods_for_view(node: ast.AST | None) -> list[str]:
    if isinstance(node, ast.ClassDef):
        methods = [method.name.lower() for method in iter_class_methods(node) if method.name.lower() in _HTTP_METHODS]
        return methods or ["get"]
    return ["get"]


def _resolve_class(resolver: ModuleResolver, file_path: Path, imports: ImportMap, expr: ast.AST) -> tuple[str | None, Path | None, ast.ClassDef | None]:
    target = expr
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "as_view":
        target = expr.func.value
    resolved = imports.resolve_name(target)
    if resolved is None and isinstance(target, ast.Name):
        resolved = (None, target.id)
    if resolved is None:
        return (None, None, None)
    module_name, symbol = resolved
    symbol = symbol or (module_name.split(".")[-1] if module_name else None)
    if module_name:
        module_path = resolver.resolve_module(module_name, from_file=file_path)
    else:
        module_path = file_path
    if not symbol or not module_path:
        return (symbol, module_path, None)
    return (symbol, module_path, resolver.find_class(module_path, symbol))


def _endpoints_from_add_url_rule(
    call: ast.Call,
    *,
    global_apps: dict[str, str],
    global_blueprints: dict[str, _BlueprintInfo],
    imports: ImportMap,
    file_path: Path,
    resolver: ModuleResolver,
) -> list[dict[str, Any]]:
    if not isinstance(call.func, ast.Attribute):
        return []
    variable = dotted_name(call.func.value)
    if not variable:
        return []

    if variable in global_apps:
        prefix = global_apps[variable]
    elif variable in global_blueprints:
        bp = global_blueprints[variable]
        prefix = bp.prefix + bp.registration_prefix
    else:
        mod_name, symbol = _resolve_blueprint(imports, variable)
        if mod_name and symbol:
            if symbol in global_apps:
                prefix = global_apps[symbol]
            elif symbol in global_blueprints:
                bp = global_blueprints[symbol]
                prefix = bp.prefix + bp.registration_prefix
            else:
                return []
        else:
            return []

    if not call.args:
        return []
    route = string_arg(call.args[0])
    if route is None:
        return []

    path, converters = _normalize_route(prefix, route)

    view_expr = kwarg(call, "view_func")
    if view_expr is None and len(call.args) >= 3:
        view_expr = call.args[2]

    symbol = None
    methods = kwarg_list_strings(call, "methods")
    if view_expr is not None:
        symbol, _, class_node = _resolve_class(resolver, file_path, imports, view_expr)
        if methods is None:
            methods = _methods_for_view(class_node)
    if methods is None:
        methods = ["GET"]
    if symbol is None:
        symbol = kwarg_string(call, "endpoint")

    endpoints: list[dict[str, Any]] = []
    for method in methods:
        endpoints.append(
            {
                "url": {"url": path, "parameter": _parameters(path, converters)},
                "method": method.upper(),
                "view": symbol,
                "function": symbol,
                "is_viewset": False,
                "path": str(file_path),
            }
        )
    return endpoints


def _endpoints_from_add_resource(
    call: ast.Call,
    *,
    global_apps: dict[str, str],
    file_path: Path,
    imports: ImportMap,
    resolver: ModuleResolver,
) -> list[dict[str, Any]]:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "add_resource":
        return []
    api_var = dotted_name(call.func.value)
    if not api_var or api_var not in global_apps or len(call.args) < 2:
        return []

    resource_expr = call.args[0]
    symbol, _, class_node = _resolve_class(resolver, file_path, imports, resource_expr)
    methods = _methods_for_view(class_node)
    prefix = global_apps.get(api_var, "")

    endpoints: list[dict[str, Any]] = []
    for route_node in call.args[1:]:
        route = string_arg(route_node)
        if route is None:
            continue
        path, converters = _normalize_route(prefix, route)
        for method in methods:
            endpoints.append(
                {
                    "url": {"url": path, "parameter": _parameters(path, converters)},
                    "method": method.upper(),
                    "view": symbol,
                    "function": method,
                    "is_viewset": True,
                    "path": str(file_path),
                }
            )
    return endpoints


def analyze_flask(
    project_path: Path,
    logger: Any = None,
    *,
    max_files: int = 20_000,
    max_bytes: int = 2_000_000,
) -> RawAnalysis:
    resolver = ModuleResolver(project_path, max_files=max_files, max_bytes=max_bytes)
    warnings: list[str] = []
    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    components, component_warnings = collect_components(project_path, resolver)
    warnings.extend(component_warnings)
    component_names = set(components)

    # Pass 1: collect all app/blueprint definitions and registrations
    global_apps: dict[str, str] = {}
    global_blueprints: dict[str, _BlueprintInfo] = {}

    for file_path in resolver.index.values():
        tree = resolver.tree(file_path)
        if tree is None:
            continue
        apps, blueprints, registrations = _collect_local_definitions(tree)
        for name, prefix in apps.items():
            global_apps[name] = prefix
        for name, prefix in blueprints.items():
            global_blueprints[name] = _BlueprintInfo(prefix=prefix)
        for name, reg_prefix in registrations.items():
            if name in global_blueprints:
                global_blueprints[name].registration_prefix = reg_prefix

    # Pass 2: extract endpoints using global registry
    for file_path in resolver.index.values():
        tree = resolver.tree(file_path)
        if tree is None:
            continue
        imports = ImportMap.from_tree(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for endpoint in _endpoint_from_decorator(
                    node,
                    global_apps=global_apps,
                    global_blueprints=global_blueprints,
                    imports=imports,
                    file_path=file_path,
                    component_names=component_names,
                ):
                    key = (endpoint["method"], endpoint["url"]["url"])
                    if key not in seen:
                        seen.add(key)
                        endpoints.append(endpoint)
            elif isinstance(node, ast.ClassDef):
                for endpoint in _endpoints_from_class(
                    node,
                    global_apps=global_apps,
                    global_blueprints=global_blueprints,
                    imports=imports,
                    file_path=file_path,
                    component_names=component_names,
                ):
                    key = (endpoint["method"], endpoint["url"]["url"])
                    if key not in seen:
                        seen.add(key)
                        endpoints.append(endpoint)
            elif isinstance(node, ast.Call):
                candidates: list[dict[str, Any]] = []
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "add_url_rule":
                        candidates = _endpoints_from_add_url_rule(
                            node,
                            global_apps=global_apps,
                            global_blueprints=global_blueprints,
                            imports=imports,
                            file_path=file_path,
                            resolver=resolver,
                        )
                    elif node.func.attr == "add_resource":
                        candidates = _endpoints_from_add_resource(
                            node,
                            global_apps=global_apps,
                            file_path=file_path,
                            imports=imports,
                            resolver=resolver,
                        )
                for endpoint in candidates:
                    key = (endpoint["method"], endpoint["url"]["url"])
                    if key not in seen:
                        seen.add(key)
                        endpoints.append(endpoint)

    if not endpoints:
        warnings.append(
            "No Flask routes were detected. Static extraction supports @app.route, "
            "@bp.route, add_url_rule, flask_restful add_resource and flask-restx "
            "Namespace routes."
        )

    return RawAnalysis(
        framework="flask",
        endpoints=endpoints,
        components=components,
        warnings=warnings,
        metadata={"endpoint_source": "static", "component_source": "static"},
    )