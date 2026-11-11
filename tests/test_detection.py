from __future__ import annotations

from pathlib import Path

from pyopenapi.detection import detect_framework


def test_detects_django(django_project: Path) -> None:
    result = detect_framework(django_project)
    assert result.framework == "django"
    assert result.supported is True
    assert result.confidence >= 0.5


def test_detects_flask(flask_project: Path) -> None:
    result = detect_framework(flask_project)
    assert result.framework == "flask"
    assert result.supported is True


def test_detects_fastapi(fastapi_project: Path) -> None:
    result = detect_framework(fastapi_project)
    assert result.framework == "fastapi"
    assert result.supported is True


def test_unknown_directory(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    result = detect_framework(tmp_path)
    assert result.framework == "unknown"
    assert result.supported is False
