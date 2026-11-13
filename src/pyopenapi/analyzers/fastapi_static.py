"""Dependency-free static FastAPI endpoint extraction (preview).

FastAPI can emit its own OpenAPI schema at runtime, but that requires importing
the application. This extractor works purely from the AST and covers:

* ``FastAPI`` apps and ``APIRouter`` instances (including ``prefix=``)
* ``app.include_router(router, prefix=...)`` (same or cross module)
* all HTTP verb decorators and ``@app.api_route(methods=[...])``
* path/query parameters and Pydantic request/response models
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .ast_utils import (
    ImportMap,
    ModuleResolver,
    annotation_text,
    dotted_name,
    kwarg,
    kwarg_list_strings,
    kwarg_string,
    string_arg,
)
from .base import RawAnalysis
from .schema_static import collect_components

_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_SIMPLE_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "UUID": "string",
    "datetime": "string",
    "date": "string",
}


def _normalize_route(prefix: str, route: str) -> tuple[str, dict[str, str]]:
    converters: dict[str, str] = {}
    combined = "/" + "/".join(part.strip("/") for part in (prefix, route) if part and part.strip("/"))
    combined = re.sub(r"/{2,}", "/", combined) or "/"
    for name in re.findall(r"\{([^{}]+)\}", combined):
        converters[name] = "string"
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


def _collect_definitions(
    resolver: ModuleResolver,
) -> tuple[dict[Path, dict[str, str]], dict[Path, set[str]]]:
    """Return per-file ``router_var -> prefix`` and ``app_vars``."""

    routers: dict[Path, dict[str, str]] = {}
    apps: dict[Path, set[str]] = {}
    for path in resolver.index.values():
        tree = resolver.tree(path)
        if tree is None:
            continue
        file_routers: dict[str, str] = {}
        file_apps: set[str] = set()
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            ctor = (dotted_name(value.func) or "").split(".")[-1]
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if ctor == "APIRouter":
                prefix = kwarg_string(value, "prefix") or ""
                for name in names:
                    file_routers[name] = prefix
            elif ctor == "FastAPI":
                for name in names:
                    file_apps.add(name)
        if file_routers:
            routers[path] = file_routers
        if file_apps:
            apps[path] = file_apps
    return routers, apps


def _resolve_router_target(
    expr: ast.AST,
    file_path: Path,
    imports: ImportMap,
    resolver: ModuleResolver,
) -> tuple[Path, str | None]:
    if isinstance(expr, ast.Name):
        return (file_path, expr.id)
    resolved = imports.resolve_name(expr)
    if resolved is None:
        return (file_path, dotted_name(expr))
    module_name, symbol = resolved
    if not module_name:
        return (file_path, symbol)
    module_path = resolver.resolve_module(module_name, from_file=file_path)
    return (module_path or file_path, symbol)


def _collect_includes(
    resolver: ModuleResolver,
) -> tuple[dict[tuple[Path, str], str], dict[Path, dict[str, str]]]:
    """Return global include prefixes and per-file local include prefixes."""

    global_prefixes: dict[tuple[Path, str], str] = {}
    local_prefixes: dict[Path, dict[str, str]] = {}

    for path in resolver.index.values():
        tree = resolver.tree(path)
        if tree is None:
            continue
        imports = ImportMap.from_tree(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
                continue
            if not node.args:
                continue
            target_path, symbol = _resolve_router_target(node.args[0], path, imports, resolver)
            prefix = kwarg_string(node, "prefix") or ""
            if symbol is None:
                continue
            if target_path == path:
                local_prefixes.setdefault(path, {})[symbol] = local_prefixes.get(path, {}).get(symbol, "") + prefix
            else:
                key = (target_path, symbol)
                global_prefixes[key] = global_prefixes.get(key, "") + prefix
    return global_prefixes, local_prefixes


def _annotation_type_name(annotation: ast.AST | None) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        base = dotted_name(annotation.value)
        return base.split(".")[-1] if base else None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _annotation_type_name(annotation.left) or _annotation_type_name(annotation.right)
    return None


def _annotation_openapi_type(annotation: ast.AST | None) -> str:
    name = _annotation_type_name(annotation)
    return _SIMPLE_TYPES.get(name or "", "string")


def _response_annotation_models(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    component_names: set[str],
) -> tuple[str | None, bool]:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        response_expr = kwarg(decorator, "response_model")
        if response_expr is None:
            continue
        if isinstance(response_expr, ast.Subscript):
            base = dotted_name(response_expr.value) or ""
            if base.split(".")[-1] in {"list", "List", "Sequence"}:
                inner = response_expr.slice
                inner_name = _annotation_type_name(inner)
                if inner_name in component_names:
                    return inner_name, True
        name = _annotation_type_name(response_expr)
        if name in component_names:
            return name, False
    return None, False


def _call_name(expr: ast.AST | None) -> str | None:
    if isinstance(expr, ast.Call):
        return (dotted_name(expr.func) or "").split(".")[-1]
    return None


def _unwrap_annotated(annotation: ast.AST | None) -> tuple[ast.AST | None, list[ast.AST]]:
    if isinstance(annotation, ast.Subscript):
        base = (dotted_name(annotation.value) or "").split(".")[-1]
        if base == "Annotated":
            slice_node = annotation.slice
            elements = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
            if elements:
                return elements[0], list(elements[1:])
    return annotation, []


_LOCATION_CALLS = {
    "Path": "path",
    "Query": "query",
    "Header": "header",
    "Cookie": "cookie",
    "Form": "form",
    "File": "file",
    "Body": "body",
}


def _param_details(
    name: str,
    annotation: ast.AST | None,
    default: ast.AST | None,
) -> tuple[str, ast.AST | None, bool, str | None, list[ast.AST]]:
    """Return (location, schema_annotation, required, dependency_name, metadata)."""

    inner, metadata = _unwrap_annotated(annotation)
    location = "query"
    required = default is None
    dependency: str | None = None

    candidates: list[ast.AST] = [*metadata]
    if default is not None:
        candidates.append(default)

    for candidate in candidates:
        call = _call_name(candidate)
        if call in _LOCATION_CALLS:
            location = _LOCATION_CALLS[call]
            if isinstance(candidate, ast.Call):
                required_kw = kwarg(candidate, "required")
                if isinstance(required_kw, ast.Constant) and isinstance(required_kw.value, bool):
                    required = required_kw.value
                elif location == "path":
                    required = True
            if call == "Body":
                inner_type = _annotation_type_name(inner)
                if inner_type and not isinstance(inner, ast.Subscript):
                    location = "body"
        elif call in {"Depends", "Security"}:
            dependency = _dependency_name(candidate)
            if call == "Security":
                location = "security"

    if name in {"request", "response"}:
        location = "skip"
    return location, inner, required, dependency, metadata


def _dependency_name(call: ast.AST) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    explicit = kwarg_string(call, "scheme_name")
    if explicit:
        return explicit
    if call.args:
        name = _annotation_type_name(call.args[0])
        if name:
            return _clean_scheme_name(name)
    return None


def _clean_scheme_name(name: str) -> str:
    lowered = name.lower()
    for prefix in ("get_", "verify_", "require_", "check_", "validate_"):
        if lowered.startswith(prefix):
            name = name[len(prefix) :]
            lowered = name.lower()
    for suffix in ("_header", "_dependency", "_dep"):
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            lowered = name.lower()
    return name or "api_key"


def _security_scheme(name: str) -> dict[str, Any]:
    lowered = name.lower()
    if "oauth" in lowered:
        return {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "/oauth/authorize",
                    "tokenUrl": "/oauth/token",
                    "scopes": {},
                }
            },
        }
    if "basic" in lowered:
        return {"type": "http", "scheme": "basic"}
    if any(token in lowered for token in ("bearer", "jwt", "token")):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    return {"type": "apiKey", "in": "header", "name": "X-API-Key"}


def _collect_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    component_names: set[str],
    path_param_names: set[str],
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    request_model: str | None = None
    extra_params: list[dict[str, Any]] = []
    security: list[dict[str, Any]] = []
    security_schemes: dict[str, Any] = {}

    args = [*node.args.args, *node.args.kwonlyargs]
    defaults: list[ast.AST | None] = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
    default_by_name = {arg.arg: default for arg, default in zip(args, defaults, strict=False)}
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False):
        default_by_name[arg.arg] = default

    for arg in args:
        name = arg.arg
        if name in {"self", "cls"}:
            continue
        annotation = arg.annotation
        default = default_by_name.get(name)
        location, inner, required, dependency, _metadata = _param_details(name, annotation, default)

        if location == "skip":
            continue
        if location == "security":
            scheme = dependency or "api_key"
            security.append({scheme: []})
            security_schemes.setdefault(scheme, _security_scheme(scheme))
            continue
        if location in {"path"} or name in path_param_names:
            continue
        if dependency is not None:
            # Non-security dependencies are not part of the request surface.
            continue

        type_name = _annotation_type_name(inner)
        if type_name in component_names:
            request_model = request_model or type_name
            continue
        if type_name is None:
            continue

        # Annotated[T, Body(...)] / T = Body(...) -> JSON body property.
        if location == "body":
            request_model = request_model or "__inline_body__"
            extra_params.append(
                {
                    "name": name,
                    "location": "body",
                    "required": required,
                    "type": _SIMPLE_TYPES.get(type_name, "string"),
                }
            )
            continue

        extra_params.append(
            {
                "name": name,
                "location": location,
                "required": required if location in {"path", "header"} else False,
                "type": _SIMPLE_TYPES.get(type_name, "string"),
            }
        )

    return request_model, extra_params, security, security_schemes


def analyze_fastapi(
    project_path: Path,
    logger: Any = None,
    *,
    max_files: int = 20_000,
    max_bytes: int = 2_000_000,
) -> RawAnalysis:
    resolver = ModuleResolver(project_path, max_files=max_files, max_bytes=max_bytes)
    warnings: list[str] = []

    components, component_warnings = collect_components(
        project_path, resolver, include_pydantic=True, include_marshmallow=False
    )
    warnings.extend(component_warnings)
    component_names = set(components)

    routers, apps = _collect_definitions(resolver)
    global_includes, local_includes = _collect_includes(resolver)

    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for file_path in resolver.index.values():
        tree = resolver.tree(file_path)
        if tree is None:
            continue
        file_routers = routers.get(file_path, {})
        file_apps = apps.get(file_path, set())
        if not file_routers and not file_apps:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            response_model, response_many = _response_annotation_models(node, component_names)
            annotations = {
                arg.arg: arg.annotation for arg in node.args.args if arg.arg not in {"self", "cls"}
            }

            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func_name = dotted_name(decorator.func) or ""
                parts = func_name.split(".")
                if len(parts) < 2:
                    continue
                variable, verb = parts[-2], parts[-1]
                if variable not in file_routers and variable not in file_apps:
                    continue
                if verb not in _VERBS and verb != "api_route":
                    continue
                if not decorator.args:
                    continue
                route = string_arg(decorator.args[0])
                if route is None:
                    continue

                if variable in file_routers:
                    prefix = file_routers.get(variable, "")
                    prefix += local_includes.get(file_path, {}).get(variable, "")
                    prefix += global_includes.get((file_path, variable), "")
                else:
                    prefix = ""

                path, converters = _normalize_route(prefix, route)
                path_param_names = set(converters)
                for name in path_param_names:
                    converters[name] = _annotation_openapi_type(annotations.get(name))
                request_model, extra_params, security, security_schemes = _collect_parameters(
                    node, component_names, path_param_names
                )
                if request_model == "__inline_body__":
                    request_model = None
                if verb == "api_route":
                    methods = kwarg_list_strings(decorator, "methods") or ["GET"]
                else:
                    methods = [verb.upper()]

                parameters = _parameters(path, converters) + extra_params
                for method in methods:
                    key = (method.upper(), path)
                    if key in seen:
                        continue
                    seen.add(key)
                    endpoint = {
                        "url": {"url": path, "parameter": parameters},
                        "method": method.upper(),
                        "view": node.name,
                        "function": node.name,
                        "is_viewset": False,
                        "path": str(file_path),
                        "request_model": request_model,
                        "response_model": response_model,
                        "response_annotation": annotation_text(node.returns),
                    }
                    if response_many:
                        endpoint["response_many"] = True
                    if security:
                        endpoint["security"] = security
                        endpoint["security_schemes"] = security_schemes
                    endpoints.append(endpoint)

    if not endpoints:
        warnings.append(
            "No FastAPI routes were detected. Static extraction supports @app.<verb>, "
            "@router.<verb>, api_route and include_router(prefix=...) patterns."
        )

    return RawAnalysis(
        framework="fastapi",
        endpoints=endpoints,
        components=components,
        warnings=warnings,
        metadata={"endpoint_source": "static", "component_source": "static"},
    )
