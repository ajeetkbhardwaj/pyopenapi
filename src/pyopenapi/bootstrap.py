"""Locate the knowl-apidocs sources and make them importable.

The legacy analysers live in ``common/`` and ``genapidocs_v2/`` at the root of
the knowl-apidocs repository. They are not a distributable package, so the MCP
server bootstraps ``sys.path`` at runtime. A ``PYOPENAPI_KNOWL_ROOT`` (or
``KNOWL_APIDOCS_ROOT``) environment variable can point at the repository when
pyopenapi is installed elsewhere.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

_REQUIRED_DIRS = ("common", "genapidocs_v2")


class KnowlRootNotFound(RuntimeError):
    """Raised when the knowl-apidocs repository cannot be located."""


def _is_knowl_root(path: Path) -> bool:
    return all((path / name).is_dir() for name in _REQUIRED_DIRS)


def _iter_candidates() -> list[Path]:
    candidates: list[Path] = []

    for env_name in ("PYOPENAPI_KNOWL_ROOT", "KNOWL_APIDOCS_ROOT"):
        raw = os.getenv(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    # Walk up from this file: works for both source checkouts and editable installs.
    here = Path(__file__).resolve()
    candidates.extend(here.parents)

    # Walk up from the current working directory.
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])

    return candidates


@lru_cache(maxsize=1)
def find_knowl_root() -> Path:
    """Return the resolved knowl-apidocs repository root."""

    seen: set[Path] = set()
    for candidate in _iter_candidates():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _is_knowl_root(resolved):
            return resolved

    raise KnowlRootNotFound(
        "Could not locate the knowl-apidocs repository (directories 'common' and "
        "'genapidocs_v2' were not found). Set PYOPENAPI_KNOWL_ROOT to the repository "
        "root, or run pyopenapi from inside the repository."
    )


def _analyzer_dirs() -> list[Path]:
    """Locate installed analyzer modules via the import system."""

    import importlib.util

    dirs: list[Path] = []
    for module in ("django_analyzer", "python_analyzer"):
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            continue
        if spec and spec.origin:
            try:
                dirs.append(Path(spec.origin).resolve().parent)
            except OSError:
                continue
    return dirs


def ensure_knowl_on_path() -> Path:
    """Insert the repository (and ``genapidocs_v2``) into ``sys.path``."""

    try:
        root = find_knowl_root()
    except KnowlRootNotFound:
        analyzer_dirs = _analyzer_dirs()
        if not analyzer_dirs:
            raise
        for entry in analyzer_dirs:
            value = str(entry)
            if value not in sys.path:
                sys.path.insert(0, value)
        return analyzer_dirs[0]

    for entry in (root, root / "genapidocs_v2"):
        value = str(entry)
        if value not in sys.path:
            sys.path.insert(0, value)
    return root


def load_env() -> Path | None:
    """Load a ``.env`` file into ``os.environ`` without overriding real env vars.

    Precedence: ``PYOPENAPI_ENV_FILE`` if set, then ``./.env``, then the
    repository/package-root ``.env``. Returns the file that was loaded.
    """

    try:
        from dotenv import load_dotenv
    except Exception:
        return None

    candidates: list[Path] = []
    override = os.getenv("PYOPENAPI_ENV_FILE")
    if override:
        candidates.append(Path(override).expanduser())
    else:
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path(__file__).resolve().parents[2] / ".env")

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            load_dotenv(resolved, override=False)
        except Exception:
            continue
        return resolved
    return None
