from __future__ import annotations

import os
from pathlib import Path

from pyopenapi.bootstrap import load_env


def test_load_env_reads_dotenv_from_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PYOPENAPI_TEST_KEY", raising=False)
    monkeypatch.delenv("PYOPENAPI_ENV_FILE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PYOPENAPI_TEST_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    loaded = load_env()

    assert loaded == env_file.resolve()
    assert os.environ["PYOPENAPI_TEST_KEY"] == "from-dotenv"
    monkeypatch.delenv("PYOPENAPI_TEST_KEY", raising=False)


def test_load_env_does_not_override_existing_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYOPENAPI_TEST_KEY", "from-process")
    monkeypatch.delenv("PYOPENAPI_ENV_FILE", raising=False)
    (tmp_path / ".env").write_text("PYOPENAPI_TEST_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    load_env()

    assert os.environ["PYOPENAPI_TEST_KEY"] == "from-process"


def test_load_env_honors_explicit_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PYOPENAPI_TEST_KEY", raising=False)
    custom = tmp_path / "custom.env"
    custom.write_text("PYOPENAPI_TEST_KEY=custom\n", encoding="utf-8")
    monkeypatch.setenv("PYOPENAPI_ENV_FILE", str(custom))

    loaded = load_env()

    assert loaded == custom.resolve()
    assert os.environ["PYOPENAPI_TEST_KEY"] == "custom"
    monkeypatch.delenv("PYOPENAPI_TEST_KEY", raising=False)
