"""AST helpers shared by the static framework extractors."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    "dist",
    "build",
    ".eggs",
}


def iter_python_files(root: Path, *, max_files: int = 20_000, max_bytes: int = 2_000_000):
    import os

    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            seen += 1
            if seen > max_files:
                return
            yield path


def parse_file(path: Path) -> ast.Module | None:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError:
        return None


def dotted_name(node: ast.AST) -> str | None:
    """Return a dotted name for Name/Attribute chains, else ``None``."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def string_arg(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def kwarg_string(call: ast.Call, name: str) -> str | None:
    return string_arg(kwarg(call, name))


def kwarg_list_strings(call: ast.Call, name: str) -> list[str] | None:
    value = kwarg(call, name)
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return None
    items: list[str] = []
    for element in value.elts:
        text = string_arg(element)
        if text is not None:
            items.append(text)
    return items


def decorator_name(node: ast.AST) -> str | None:
    """Return the dotted name of a decorator, unwrapping calls."""

    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return dotted_name(node)


def class_bases(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        text = dotted_name(base)
        if text:
            names.append(text.split(".")[-1])
    return names


def function_arg_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = [arg.arg for arg in node.args.posonlyargs + node.args.args]
    if args and args[0] in {"self", "cls"}:
        args = args[1:]
    return args


def annotation_text(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - very old AST shapes
        return None


@dataclass
class ImportMap:
    """Maps local aliases to ``(module, imported_name)`` pairs.

    ``imported_name`` is ``None`` for plain module imports and the symbol name
    for ``from module import symbol`` statements.
    """

    aliases: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    @classmethod
    def from_tree(cls, tree: ast.Module) -> "ImportMap":
        result = cls()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = "." * node.level + module
                for alias in node.names:
                    local = alias.asname or alias.name
                    result.aliases[local] = (module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[-1]
                    result.aliases[local] = (alias.name, None)
        return result

    def resolve_name(self, node: ast.AST) -> tuple[str | None, str | None] | None:
        """Resolve an expression to ``(module, symbol)`` when possible."""

        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve_name(node.value)
            if base is None:
                return None
            module, symbol = base
            if symbol is None:
                return (module, node.attr)
            # ``symbol`` was imported from ``module``; treat the attribute as a
            # member of that symbol (e.g. ``views.MyView`` -> module.views, MyView).
            if module:
                joined = f"{module.rstrip('.')}.{symbol}"
                return (joined, node.attr)
            return (symbol, node.attr)
        return None


class ModuleResolver:
    """Resolves dotted module names and symbols to files under a project root."""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 20_000,
        max_bytes: int = 2_000_000,
    ) -> None:
        self.root = root.resolve()
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._path_cache: dict[str, Path | None] = {}
        self._tree_cache: dict[Path, ast.Module | None] = {}
        self._index: dict[str, Path] | None = None

    def _build_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        for path in iter_python_files(self.root, max_files=self.max_files, max_bytes=self.max_bytes):
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                continue
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            module = ".".join(parts)
            index.setdefault(module, path)
        return index

    @property
    def index(self) -> dict[str, Path]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def package_of(self, file_path: Path) -> str:
        try:
            rel = file_path.resolve().relative_to(self.root)
        except ValueError:
            return ""
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        else:
            parts = parts[:-1]
        return ".".join(parts)

    def resolve_module(self, module: str, *, from_file: Path | None = None) -> Path | None:
        cache_key = f"{module}|{from_file}"
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]

        resolved = self._resolve_module_uncached(module, from_file)
        self._path_cache[cache_key] = resolved
        return resolved

    def _resolve_module_uncached(self, module: str, from_file: Path | None) -> Path | None:
        if not module:
            return None

        if module.startswith("."):
            level = len(module) - len(module.lstrip("."))
            remainder = module[level:]
            base = (from_file.parent if from_file else self.root).resolve()
            for _ in range(level - 1):
                base = base.parent
            parts = [part for part in remainder.split(".") if part]
            return self._candidate(base, parts)

        parts = [part for part in module.split(".") if part]
        direct = self._candidate(self.root, parts)
        if direct:
            return direct

        # Fall back to the precomputed index (handles src/ layouts).
        return self.index.get(module)

    @staticmethod
    def _candidate(base: Path, parts: list[str]) -> Path | None:
        if not parts:
            init = base / "__init__.py"
            return init if init.is_file() else None
        file_candidate = base.joinpath(*parts).with_suffix(".py")
        if file_candidate.is_file():
            return file_candidate
        init_candidate = base.joinpath(*parts) / "__init__.py"
        if init_candidate.is_file():
            return init_candidate
        return None

    def tree(self, path: Path) -> ast.Module | None:
        resolved = path.resolve()
        if resolved not in self._tree_cache:
            self._tree_cache[resolved] = parse_file(resolved)
        return self._tree_cache[resolved]

    def find_class(self, file_path: Path, class_name: str) -> ast.ClassDef | None:
        tree = self.tree(file_path)
        if tree is None:
            return None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None

    def find_function(self, file_path: Path, function_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        tree = self.tree(file_path)
        if tree is None:
            return None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                return node
        return None


def iter_class_methods(class_node: ast.ClassDef) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
