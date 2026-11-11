"""Resolve project sources from local paths or GitHub repository URLs."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import Settings, get_settings
from .errors import AnalysisError
from .utils import ensure_directory, resolve_project_path, short_hash

_OWNER_REPO = re.compile(r"^[A-Za-z0-9_.-]+$")


def parse_github_source(raw: str) -> tuple[str, str] | None:
    """Return ``(owner, repo)`` for a GitHub URL/shorthand, else ``None``.

    Supported forms:
    ``https://github.com/owner/repo``, ``.../repo.git``, ``.../tree/branch``,
    ``git@github.com:owner/repo.git``, ``github.com/owner/repo``,
    ``github:owner/repo`` and ``gh:owner/repo``.
    """

    value = raw.strip()
    if not value:
        return None

    path: str | None = None
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    elif value.startswith(("github:", "gh:")):
        path = value.split(":", 1)[1]
    elif "github.com/" in value:
        path = value.split("github.com/", 1)[1]

    if path is None:
        return None

    path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo or not _OWNER_REPO.match(owner) or not _OWNER_REPO.match(repo):
        return None
    return owner, repo


def is_github_source(raw: str) -> bool:
    return parse_github_source(raw) is not None


def _github_token(settings: Settings) -> str | None:
    return (
        settings.github_token
        or os.getenv("PYOPENAPI_GITHUB_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
    )


def _github_ssh_key(settings: Settings) -> str | None:
    return settings.github_ssh_key or os.getenv("PYOPENAPI_GITHUB_SSH_KEY")


def _redact(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "***")
    return text


def _clone_with_git_cli(url: str, target: Path, *, ssh_key: str | None) -> None:
    """Clone using the system ``git`` binary (supports SSH keys and tokens)."""

    env = dict(os.environ)
    if ssh_key:
        import shlex

        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {shlex.quote(ssh_key)} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=accept-new"
        )
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git clone failed")


def _clone_with_dulwich(url: str, target: Path) -> None:
    from dulwich import porcelain

    err = io.BytesIO()
    try:
        porcelain.clone(url, str(target), depth=1, errstream=err)
    except Exception as exc:
        detail = err.getvalue().decode("utf-8", "replace").strip()
        raise RuntimeError(detail or str(exc)) from exc


def _clone_github(owner: str, repo: str, settings: Settings, *, refresh: bool) -> Path:
    cache_root = ensure_directory(settings.resolved_cache_dir() / "repos")
    # Cache key uses owner/repo (never the credential-bearing URL).
    target = cache_root / f"{owner}__{repo}__{short_hash(owner, repo)}"

    if target.exists() and not refresh:
        return target
    if target.exists():
        shutil.rmtree(target)

    token = _github_token(settings)
    ssh_key = _github_ssh_key(settings)
    https_url = f"https://github.com/{owner}/{repo}.git"
    ssh_url = f"git@github.com:{owner}/{repo}.git"

    errors: list[str] = []
    # 1) SSH key configured -> use the git CLI over SSH.
    if ssh_key:
        try:
            _clone_with_git_cli(ssh_url, target, ssh_key=ssh_key)
            if target.is_dir():
                return target
        except Exception as exc:
            errors.append(_redact(str(exc), None))

    # 2) Token configured -> authenticated HTTPS clone.
    if token:
        auth_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        for cloner in (
            lambda: _clone_with_git_cli(auth_url, target, ssh_key=None),
            lambda: _clone_with_dulwich(auth_url, target),
        ):
            try:
                cloner()
                if target.is_dir():
                    return target
            except Exception as exc:
                errors.append(_redact(str(exc), token))
                shutil.rmtree(target, ignore_errors=True)

    # 3) Anonymous clone (public repositories).
    for cloner in (
        lambda: _clone_with_dulwich(https_url, target),
        lambda: _clone_with_git_cli(https_url, target, ssh_key=None),
    ):
        try:
            cloner()
            if target.is_dir():
                return target
        except Exception as exc:
            errors.append(_redact(str(exc), None))
            shutil.rmtree(target, ignore_errors=True)

    detail = "; ".join(item for item in errors if item) or "unknown clone error"
    raise AnalysisError(
        f"Could not clone GitHub repository {owner}/{repo}: {detail}",
        hint=(
            "Public repos need network access. For private repos set PYOPENAPI_GITHUB_TOKEN "
            "(or GITHUB_TOKEN) for HTTPS, or PYOPENAPI_GITHUB_SSH_KEY to a private key file "
            "for SSH. Alternatively clone locally and pass the local path."
        ),
        details={"owner": owner, "repo": repo},
    )


def resolve_project_source(
    raw: str | Path,
    settings: Settings | None = None,
    *,
    refresh: bool = False,
) -> Path:
    """Resolve a local project path or clone a GitHub repository into the cache."""

    text = str(raw).strip()
    local = Path(text).expanduser()
    if local.exists() or not is_github_source(text):
        return resolve_project_path(text)

    parsed = parse_github_source(text)
    if parsed is None:  # pragma: no cover - guarded by is_github_source
        return resolve_project_path(text)
    owner, repo = parsed
    return _clone_github(owner, repo, settings or get_settings(), refresh=refresh)
