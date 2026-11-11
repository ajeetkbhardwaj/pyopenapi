"""Small shared helpers used across pyopenapi."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short_hash(*parts: object, length: int = 16) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\x00")
    return digest.hexdigest()[:length]


def slugify(value: str, *, fallback: str = "project") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug.lower() or fallback


def jsonable(value: Any, *, depth: int = 0, max_depth: int = 12) -> Any:
    """Best-effort conversion of arbitrary analyser output to JSON types."""

    if depth > max_depth:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {
            str(key): jsonable(item, depth=depth + 1, max_depth=max_depth)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item, depth=depth + 1, max_depth=max_depth) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value), depth=depth + 1, max_depth=max_depth)
    if hasattr(value, "model_dump"):
        try:
            return jsonable(value.model_dump(), depth=depth + 1, max_depth=max_depth)
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def resolve_project_path(raw: str | os.PathLike[str]) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path)
    return path.resolve()


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class _StdoutToStderr:
    """File-like proxy that routes text to stderr but preserves ``.buffer``.

    The legacy analysers print progress to stdout. Under the stdio transport
    that would corrupt MCP protocol frames, so tool execution wraps them with
    :func:`redirect_stdout_to_stderr`. The MCP server itself holds a reference to
    the original ``stdout`` buffer, which we expose unchanged.
    """

    def __init__(self, original: Any) -> None:
        self._original = original

    def write(self, data: str) -> int:
        return sys.stderr.write(data)

    def writelines(self, lines: Any) -> None:
        sys.stderr.writelines(lines)

    def flush(self) -> None:
        sys.stderr.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return sys.stderr.fileno()

    @property
    def buffer(self) -> Any:
        return getattr(self._original, "buffer", self._original)

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return getattr(self._original, "errors", "replace")

    def __getattr__(self, item: str) -> Any:
        return getattr(self._original, item)


def install_stdout_guard() -> None:
    """Permanently route ``print()`` to stderr while preserving ``.buffer``.

    Call this once when starting the MCP server. The stdio transport writes
    protocol frames through ``sys.stdout.buffer``; the proxy keeps that buffer
    pointing at the real stdout while sending stray ``print()`` output to
    stderr. A process-wide guard avoids races between concurrent tool calls.
    """

    if not isinstance(sys.stdout, _StdoutToStderr):
        sys.stdout = _StdoutToStderr(sys.stdout)


@contextlib.contextmanager
def redirect_stdout_to_stderr() -> Iterator[None]:
    """Route ``print()`` output to stderr for the duration of the block."""

    original = sys.stdout
    if isinstance(original, _StdoutToStderr):  # already redirected
        yield
        return
    sys.stdout = _StdoutToStderr(original)
    try:
        yield
    finally:
        sys.stdout = original


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float = 900.0, poll: float = 0.25) -> Iterator[None]:
    """Advisory inter-process lock with a bounded wait.

    Prevents two concurrent MCP calls from analysing the same project twice.
    Falls back to a no-op on platforms without ``fcntl``.
    """

    if fcntl is None:  # pragma: no cover
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for lock: {path}")
                time.sleep(poll)
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Stopwatch:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)

    def reset(self) -> None:
        self._start = time.perf_counter()


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n... (truncated) ..."


def dir_size(path: Path) -> int:
    """Return the total size in bytes of all files under ``path``."""

    total = 0
    if not path.exists():
        return 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def human_bytes(value: float) -> str:
    """Format a byte count as a human-readable string."""

    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} PiB"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
