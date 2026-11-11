from __future__ import annotations

from pathlib import Path

import pytest

from pyopenapi.source import is_github_source, parse_github_source, resolve_project_source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo/tree/main/src", ("owner", "repo")),
        ("http://www.github.com/owner/repo/", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("github.com/owner/repo", ("owner", "repo")),
        ("github:owner/repo", ("owner", "repo")),
        ("gh:owner/repo", ("owner", "repo")),
    ],
)
def test_parse_github_source(raw: str, expected: tuple[str, str]) -> None:
    assert parse_github_source(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://gitlab.com/owner/repo",
        "https://github.com/owner",
        "https://github.com/",
        "/local/path/to/project",
        "./relative/project",
    ],
)
def test_non_github_sources(raw: str) -> None:
    assert is_github_source(raw) is False


def test_local_path_wins_over_url_like_name(tmp_path: Path) -> None:
    local = tmp_path / "github.com" / "owner" / "repo"
    local.mkdir(parents=True)
    assert resolve_project_source(str(local)) == local.resolve()


def test_resolve_github_uses_clone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pyopenapi import source

    target = tmp_path / "cloned"
    target.mkdir()

    def fake_clone(owner: str, repo: str, settings, *, refresh: bool) -> Path:
        assert (owner, repo) == ("owner", "repo")
        return target

    monkeypatch.setattr(source, "_clone_github", fake_clone)
    assert resolve_project_source("https://github.com/owner/repo") == target
