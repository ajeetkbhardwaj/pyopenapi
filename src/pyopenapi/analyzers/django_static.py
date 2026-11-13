"""Dependency-free static Django URL extraction.

The upstream ``DjangoAnalyzer._extract_endpoints_static`` is currently a
placeholder, and dynamic extraction requires Django (plus every project
dependency) to be importable by the *server* interpreter. This module provides a
robust AST-based fallback so analysis still works in sandboxed/CI environments.

It understands the common Django/DRF patterns:

* ``path``/``re_path``/``url`` with function, class-based and ``as_view`` views
* ``include(...)`` recursion (string modules and inline lists)
* ``DefaultRouter``/``SimpleRouter`` registrations and ``router.urls``
* ``ViewSet`` mixins and ``@action`` decorators
* ``@api_view`` / ``@require_http_methods`` method hints
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ast_utils import (
    ImportMap,
    ModuleResolver,
    class_bases,
    decorator_name,
    dotted_name,
    iter_class_methods,
    kwarg,
    kwarg_list_strings,
    kwarg_string,
    parse_file,
    string_arg,
)

_MAX_INCLUDE_DEPTH = 15
_CONVERTER_TYPES = {
    "int": "integer",
    "uuid": "string",
    "slug": "string",
    "str": "string",
    "path": "string",
}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_VIEWSET_ACTIONS = ("list", "create", "retrieve", "update", "partial_update", "destroy")
_MIXIN_ACTIONS = {
    "ListModelMixin": {"list"},
    "CreateModelMixin": {"create"},
    "RetrieveModelMixin": {"retrieve"},
    "UpdateModelMixin": {"update", "partial_update"},
    "DestroyModelMixin": {"destroy"},
}
_ACTION_HTTP = {
    "list": "get",
    "create": "post",
    "retrieve": "get",
    "update": "put",
    "partial_update": "patch",
    "destroy": "delete",
}


@dataclass
class _Context:
    resolver: ModuleResolver
    warnings: list[str] = field(default_factory=list)
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    visited: set[tuple[str, str]] = field(default_factory=set)
    seen_endpoints: set[tuple[str, str]] = field(default_factory=set)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def add_endpoint(
        self,
        *,
        method: str,
        path: str,
        view: str | None,
        function: str | None,
        is_viewset: bool,
        source_file: Path | None,
        params: dict[str, str] | None = None,
        serializer: str | None = None,
        request_serializer: str | None = None,
        response_serializer: str | None = None,
        response_many: bool = False,
        description: str | None = None,
    ) -> None:
        method = method.upper()
        key = (method, path)
        if key in self.seen_endpoints:
            return
        self.seen_endpoints.add(key)
        parameters = _parameters_from_path(path, params or {})
        endpoint = {
            "url": {"url": path, "parameter": parameters},
            "method": method,
            "view": view,
            "function": function,
            "is_viewset": is_viewset,
            "path": str(source_file) if source_file else None,
        }
        if serializer:
            endpoint["serializer"] = serializer
        if request_serializer:
            endpoint["request_serializer"] = request_serializer
        if response_serializer:
            endpoint["response_serializer"] = response_serializer
        if response_many:
            endpoint["response_many"] = True
        if description:
            endpoint["description"] = description
        self.endpoints.append(endpoint)


def _join_paths(prefix: str, route: str) -> str:
    combined = "/" + "/".join(part.strip("/") for part in (prefix, route) if part and part.strip("/"))
    if not combined.startswith("/"):
        combined = "/" + combined
    return re.sub(r"/{2,}", "/", combined) or "/"


def _normalize_django_route(prefix: str, route: str, *, is_regex: bool = False) -> tuple[str, dict[str, str]]:
    converter_types: dict[str, str] = {}

    if is_regex:
        route = route.strip()
        route = route.lstrip("^")
        if route.endswith("$"):
            route = route[:-1]
        if route.endswith(r"\Z"):
            route = route[:-2]

        def _replace(match: re.Match[str]) -> str:
            name = match.group("name")
            body = match.group("body")
            if re.fullmatch(r"\d+", body) or "0-9" in body:
                converter_types[name] = "integer"
            return "{" + name + "}"

        route = re.sub(r"\(\?P<(?P<name>[^>]+)>(?P<body>[^)]*)\)", _replace, route)
        route = re.sub(r"\((?P<body>[^)]*)\)", "", route)
    else:

        def _replace(match: re.Match[str]) -> str:
            converter = match.group("converter")
            name = match.group("name")
            if converter:
                converter_types[name] = _CONVERTER_TYPES.get(converter, "string")
            return "{" + name + "}"

        route = re.sub(
            r"<(?:(?P<converter>[a-zA-Z_][\w]*):)?(?P<name>[a-zA-Z_][\w]*)>",
            _replace,
            route,
        )

    return _join_paths(prefix, route), converter_types


def _parameters_from_path(path: str, converter_types: dict[str, str]) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    for name in re.findall(r"\{([^{}]+)\}", path):
        parameters.append(
            {
                "name": name,
                "location": "path",
                "required": True,
                "type": converter_types.get(name, "string"),
            }
        )
    return parameters


def _walk_expr(expr: ast.AST) -> Iterable[ast.AST]:
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        for element in expr.elts:
            yield from _walk_expr(element)
    elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        yield from _walk_expr(expr.left)
        yield from _walk_expr(expr.right)
    else:
        yield expr


def _is_router_attr(expr: ast.AST) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr == "urls"


def _find_root_urls(
    root: Path,
    resolver: ModuleResolver,
    explicit_urls: str | None,
    explicit_settings: str | None,
) -> Path | None:
    if explicit_urls:
        candidate = Path(explicit_urls).expanduser()
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if candidate.is_file():
            return candidate
        module_path = resolver.resolve_module(explicit_urls)
        if module_path:
            return module_path

    settings_file = _find_settings_file(root, explicit_settings)
    if settings_file:
        root_urlconf = _read_root_urlconf(settings_file)
        if root_urlconf:
            module_path = resolver.resolve_module(root_urlconf)
            if module_path:
                return module_path

    candidates: list[Path] = []
    for path in resolver.index.values():
        if path.name != "urls.py":
            continue
        tree = resolver.tree(path)
        if tree is None:
            continue
        if _has_urlpatterns(tree):
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (len(item.relative_to(root).parts), len(str(item))))
    return candidates[0]


def _find_settings_file(root: Path, explicit_settings: str | None) -> Path | None:
    if explicit_settings:
        candidate = Path(explicit_settings).expanduser()
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if candidate.is_file():
            return candidate

    candidates = sorted(root.rglob("settings.py"))
    preferred = [path for path in candidates if "site-packages" not in path.parts]
    return preferred[0] if preferred else (candidates[0] if candidates else None)


def _read_root_urlconf(settings_file: Path) -> str | None:
    tree = parse_file(settings_file)
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {target.id for target in targets if isinstance(target, ast.Name)}
            if "ROOT_URLCONF" in names:
                return string_arg(node.value)
    return None


def _has_urlpatterns(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "urlpatterns":
                    return True
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "urlpatterns":
                return True
    return False


def _collect_router_registrations(
    tree: ast.Module,
) -> tuple[dict[str, ast.Call], dict[str, list[ast.Call]]]:
    routers: dict[str, ast.Call] = {}
    registrations: dict[str, list[ast.Call]] = {}

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Call):
                fname = dotted_name(value.func) or ""
                if fname.endswith("Router"):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            routers[target.id] = value
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "register":
                router_name = dotted_name(call.func.value)
                if router_name:
                    registrations.setdefault(router_name, []).append(call)

    return routers, registrations


def _process_module(
    file_path: Path,
    prefix: str,
    ctx: _Context,
    *,
    depth: int = 0,
) -> None:
    if depth > _MAX_INCLUDE_DEPTH:
        ctx.warn(f"Maximum include depth reached while processing {file_path}")
        return

    key = (str(file_path.resolve()), prefix)
    if key in ctx.visited:
        return
    ctx.visited.add(key)

    tree = ctx.resolver.tree(file_path)
    if tree is None:
        ctx.warn(f"Could not parse {file_path}")
        return

    imports = ImportMap.from_tree(tree)
    routers, registrations = _collect_router_registrations(tree)

    def process_value(value: ast.AST) -> None:
        for item in _walk_expr(value):
            _process_url_item(item, file_path, prefix, imports, routers, registrations, ctx, depth)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "urlpatterns":
                    process_value(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "urlpatterns":
                process_value(node.value)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "append"
                and dotted_name(call.func.value) == "urlpatterns"
                and call.args
            ):
                process_value(call.args[0])


def _process_url_item(
    item: ast.AST,
    file_path: Path,
    prefix: str,
    imports: ImportMap,
    routers: dict[str, ast.Call],
    registrations: dict[str, list[ast.Call]],
    ctx: _Context,
    depth: int,
) -> None:
    if _is_router_attr(item):
        router_name = dotted_name(item.value)
        if router_name:
            _expand_router(router_name, prefix, file_path, imports, registrations, ctx, depth)
        return

    if not isinstance(item, ast.Call):
        return

    name = (dotted_name(item.func) or "").split(".")[-1]
    if name in {"path", "re_path", "url"}:
        if len(item.args) < 2:
            ctx.warn(f"Skipping malformed {name}() call in {file_path}")
            return
        route = string_arg(item.args[0]) or ""
        is_regex = name in {"re_path", "url"}
        full_prefix, converter_types = _normalize_django_route(prefix, route, is_regex=is_regex)
        view_expr = item.args[1]

        if isinstance(view_expr, ast.Call):
            callee = (dotted_name(view_expr.func) or "").split(".")[-1]
            if callee == "include":
                _process_include(view_expr, file_path, full_prefix, imports, routers, registrations, ctx, depth)
                return

        _add_view_endpoints(view_expr, file_path, full_prefix, converter_types, imports, ctx)
        return

    if name == "include" and item.args:
        _process_include(item, file_path, prefix, imports, routers, registrations, ctx, depth)


def _process_include(
    call: ast.Call,
    file_path: Path,
    prefix: str,
    imports: ImportMap,
    routers: dict[str, ast.Call],
    registrations: dict[str, list[ast.Call]],
    ctx: _Context,
    depth: int,
) -> None:
    if not call.args:
        return
    target = call.args[0]

    if _is_router_attr(target):
        router_name = dotted_name(target.value)
        if router_name:
            _expand_router(router_name, prefix, file_path, imports, registrations, ctx, depth)
        return

    module_name = string_arg(target)
    if module_name:
        resolved = ctx.resolver.resolve_module(module_name, from_file=file_path)
        if resolved is None:
            ctx.warn(f"Could not resolve include('{module_name}') from {file_path}")
            return
        _process_module(resolved, prefix, ctx, depth=depth + 1)
        return

    if isinstance(target, (ast.List, ast.Tuple)):
        for element in target.elts:
            _process_url_item(element, file_path, prefix, imports, routers, registrations, ctx, depth + 1)


def _resolve_view(
    expr: ast.AST,
    file_path: Path,
    imports: ImportMap,
    resolver: ModuleResolver,
) -> tuple[str | None, Path | None, ast.AST | None, dict[str, str] | None]:
    mapping: dict[str, str] | None = None
    target = expr

    if isinstance(expr, ast.Call):
        callee = dotted_name(expr.func) or ""
        if callee.endswith("as_view"):
            target = expr.func.value if isinstance(expr.func, ast.Attribute) else expr.func
            if expr.args and isinstance(expr.args[0], ast.Dict):
                mapping = {}
                for key_node, value_node in zip(expr.args[0].keys, expr.args[0].values, strict=False):
                    key = string_arg(key_node)
                    value = string_arg(value_node)
                    if key and value:
                        mapping[key.lower()] = value

    resolved = imports.resolve_name(target)
    if resolved is None:
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            base = target.value.id
            package = resolver.package_of(file_path)
            module_name = f"{package}.{base}" if package else base
            resolved = (module_name, target.attr)
        elif isinstance(target, ast.Name):
            resolved = (None, target.id)

    if resolved is None:
        return (None, None, None, mapping)

    module_name, symbol = resolved
    if symbol is None:
        symbol = module_name.split(".")[-1] if module_name else None

    if module_name:
        module_path = resolver.resolve_module(module_name, from_file=file_path)
        if module_path is None:
            return (symbol, None, None, mapping)
    else:
        module_path = file_path

    if symbol is None or module_path is None:
        return (symbol, module_path, None, mapping)

    node = resolver.find_class(module_path, symbol) or resolver.find_function(module_path, symbol)
    return (symbol, module_path, node, mapping)


def _class_serializer_name(
    node: ast.ClassDef,
    view_file: Path | None,
    resolver: ModuleResolver,
) -> str | None:
    if view_file is None:
        return None
    tree = resolver.tree(view_file)
    if tree is None:
        return None
    imports = ImportMap.from_tree(tree)
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name) or target.id != "serializer_class":
                continue
            resolved = imports.resolve_name(item.value)
            if resolved:
                module_name, symbol = resolved
                if symbol:
                    return symbol
                if module_name:
                    return module_name.split(".")[-1]
            return (dotted_name(item.value) or "").split(".")[-1] or None
    return None


def _methods_for_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    for decorator in node.decorator_list:
        name = (decorator_name(decorator) or "").split(".")[-1]
        call = decorator if isinstance(decorator, ast.Call) else None
        if name == "api_view" and call and call.args:
            methods = _string_list(call.args[0])
            if methods:
                return [method.lower() for method in methods]
        if name == "require_http_methods" and call and call.args:
            methods = _string_list(call.args[0])
            if methods:
                return [method.lower() for method in methods]
        if name == "require_GET":
            return ["get"]
        if name == "require_POST":
            return ["post"]
        if name == "require_safe":
            return ["get", "head"]
    return ["get"]


def _methods_for_class(node: ast.ClassDef, mapping: dict[str, str] | None) -> list[str]:
    if mapping:
        return sorted(mapping.keys())
    methods = [method.name for method in iter_class_methods(node) if method.name in _HTTP_METHODS]
    if methods:
        return sorted(set(methods))
    return ["get"]


def _string_list(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [string_arg(element) for element in node.elts]
        return [value for value in values if value]
    return None


def _add_view_endpoints(
    view_expr: ast.AST,
    file_path: Path,
    path: str,
    converter_types: dict[str, str],
    imports: ImportMap,
    ctx: _Context,
) -> None:
    symbol, view_file, node, mapping = _resolve_view(view_expr, file_path, imports, ctx.resolver)

    if isinstance(node, ast.ClassDef):
        methods = _methods_for_class(node, mapping)
        serializer = _class_serializer_name(node, view_file, ctx.resolver)
        for method in methods or ["get"]:
            ctx.add_endpoint(
                method=method,
                path=path,
                view=symbol or node.name,
                function=mapping.get(method) if mapping else None,
                is_viewset=bool(mapping),
                source_file=view_file,
                params=converter_types,
                serializer=serializer,
                request_serializer=serializer if method in {"post", "put", "patch"} else None,
                response_serializer=serializer if method != "delete" else None,
            )
        return

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for method in _methods_for_function(node):
            ctx.add_endpoint(
                method=method,
                path=path,
                view=symbol or node.name,
                function=None,
                is_viewset=False,
                source_file=view_file,
                params=converter_types,
            )
        return

    # Unresolved view: still emit a GET so the endpoint is not lost.
    if symbol:
        ctx.add_endpoint(
            method="get",
            path=path,
            view=symbol,
            function=None,
            is_viewset=False,
            source_file=view_file,
            params=converter_types,
        )
    else:
        ctx.warn(f"Could not resolve view expression in {file_path} for route {path}")


def _expand_router(
    router_name: str,
    prefix: str,
    file_path: Path,
    imports: ImportMap,
    registrations: dict[str, list[ast.Call]],
    ctx: _Context,
    depth: int,
) -> None:
    calls = registrations.get(router_name, [])
    if not calls:
        ctx.warn(f"Router '{router_name}' has no registrations in scope")
        return

    for call in calls:
        if len(call.args) < 2:
            continue
        route = string_arg(call.args[0]) or ""
        view_expr = call.args[1]
        base_path, _ = _normalize_django_route(prefix, route)

        symbol, view_file, node, _ = _resolve_view(view_expr, file_path, imports, ctx.resolver)

        if not isinstance(node, ast.ClassDef):
            ctx.warn(f"Could not resolve ViewSet '{dotted_name(view_expr) or '?'}' for router '{router_name}'")
            # Emit optimistic CRUD endpoints so the API surface is visible.
            _emit_standard_actions(
                ctx, base_path, symbol or "ViewSet", view_file, set(_VIEWSET_ACTIONS), {}, None
            )
            continue

        actions = _viewset_actions(node)
        custom = _viewset_custom_actions(node)
        serializer = _class_serializer_name(node, view_file, ctx.resolver)
        _emit_standard_actions(
            ctx, base_path, symbol or node.name, view_file, actions, custom, serializer
        )


def _viewset_actions(node: ast.ClassDef) -> set[str]:
    bases = set(class_bases(node))
    actions: set[str] = set()
    if "ModelViewSet" in bases:
        actions.update(_VIEWSET_ACTIONS)
    elif "ReadOnlyModelViewSet" in bases:
        actions.update({"list", "retrieve"})
    for base, base_actions in _MIXIN_ACTIONS.items():
        if base in bases:
            actions.update(base_actions)
    for method in iter_class_methods(node):
        if method.name in _VIEWSET_ACTIONS:
            actions.add(method.name)
    if not actions:
        actions.update(_VIEWSET_ACTIONS)
    return actions


def _viewset_custom_actions(node: ast.ClassDef) -> dict[str, dict[str, Any]]:
    custom: dict[str, dict[str, Any]] = {}
    for method in iter_class_methods(node):
        for decorator in method.decorator_list:
            name = (decorator_name(decorator) or "").split(".")[-1]
            if name != "action":
                continue
            call = decorator if isinstance(decorator, ast.Call) else None
            detail = False
            methods = ["get"]
            url_path = method.name
            if call:
                detail_node = kwarg(call, "detail")
                if isinstance(detail_node, ast.Constant) and isinstance(detail_node.value, bool):
                    detail = detail_node.value
                declared = kwarg_list_strings(call, "methods")
                if declared:
                    methods = [item.lower() for item in declared]
                url_path = kwarg_string(call, "url_path") or method.name
            custom[method.name] = {
                "detail": detail,
                "methods": methods,
                "url_path": url_path,
            }
    return custom


def _emit_standard_actions(
    ctx: _Context,
    base_path: str,
    view_name: str,
    view_file: Path | None,
    actions: set[str],
    custom: dict[str, dict[str, Any]],
    serializer: str | None,
) -> None:
    collection = base_path.rstrip("/") + "/"
    detail = collection + "{pk}/"

    for action in _VIEWSET_ACTIONS:
        if action not in actions:
            continue
        method = _ACTION_HTTP[action]
        path = collection if action in {"list", "create"} else detail
        ctx.add_endpoint(
            method=method,
            path=path,
            view=view_name,
            function=action,
            is_viewset=True,
            source_file=view_file,
            params={"pk": "string"} if "{pk}" in path else {},
            serializer=serializer,
            request_serializer=serializer if action in {"create", "update", "partial_update"} else None,
            response_serializer=serializer if action != "destroy" else None,
            response_many=action == "list",
        )

    for action_name, info in custom.items():
        url_path = str(info["url_path"]).strip("/")
        if info["detail"]:
            path = detail + url_path + "/"
            params = {"pk": "string"}
        else:
            path = collection + url_path + "/"
            params = {}
        for method in info["methods"]:
            ctx.add_endpoint(
                method=method,
                path=path,
                view=view_name,
                function=action_name,
                is_viewset=True,
                source_file=view_file,
                params=params,
                serializer=serializer,
                request_serializer=serializer if method in {"post", "put", "patch"} else None,
                response_serializer=serializer if method != "delete" else None,
            )


def extract_django_endpoints(
    project_path: str | Path,
    *,
    urls_file: str | None = None,
    settings_file: str | None = None,
    max_files: int = 20_000,
    max_bytes: int = 2_000_000,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract Django endpoints without importing the project."""

    root = Path(project_path).expanduser().resolve()
    resolver = ModuleResolver(root, max_files=max_files, max_bytes=max_bytes)
    ctx = _Context(resolver=resolver)

    root_urls = _find_root_urls(root, resolver, urls_file, settings_file)
    if root_urls is None:
        ctx.warn("Could not locate a Django root urls.py (set django_urls_file to override)")
        return [], ctx.warnings

    _process_module(root_urls, "", ctx)
    return ctx.endpoints, ctx.warnings
