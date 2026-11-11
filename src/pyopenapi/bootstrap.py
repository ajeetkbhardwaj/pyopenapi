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


def ensure_knowl_on_path() -> Path:
    """Insert the repository (and ``genapidocs_v2``) into ``sys.path``."""

    root = find_knowl_root()
    for entry in (root, root / "genapidocs_v2"):
        value = str(entry)
        if value not in sys.path:
            sys.path.insert(0, value)
    return root


def load_env() -> Path | None:
    """Load ``.env`` from the repository root and the current directory."""

    loaded: Path | None = None
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover
        return None

    try:
        root = find_knowl_root()
        env_file = root / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)
            loaded = env_file
    except KnowlRootNotFound:
        pass

    # Editable/source layout: src/pyopenapi/bootstrap.py -> project root.
    try:
        project_env = Path(__file__).resolve().parents[2] / ".env"
    except IndexError:  # pragma: no cover - defensive
        project_env = None
    if project_env and project_env.is_file():
        load_dotenv(project_env, override=False)
        loaded = loaded or project_env

    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        load_dotenv(cwd_env, override=False)
        loaded = loaded or cwd_env

    return loaded
