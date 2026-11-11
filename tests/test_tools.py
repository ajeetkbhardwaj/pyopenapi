from __future__ import annotations

from pathlib import Path

import pytest

from pyopenapi.server import tools


@pytest.mark.asyncio
async def test_full_mcp_tool_workflow(django_project: Path, isolated_settings, tmp_path: Path) -> None:
    frameworks = tools.list_frameworks()
    assert frameworks["ok"] is True
    assert {item["name"] for item in frameworks["frameworks"]} == {"django", "flask", "fastapi"}

    detection = await tools.detect_framework(str(django_project))
    assert detection["ok"] is True
    assert detection["detection"]["framework"] == "django"

    analyzed = await tools.analyze_project(str(django_project), use_dynamic=False, force=True)
    assert analyzed["ok"] is True
    analysis_id = analyzed["summary"]["analysis_id"]
    assert analyzed["summary"]["endpoint_count"] == 8

    endpoints = await tools.extract_endpoints(analysis_id, method="GET")
    assert endpoints["ok"] is True
    assert endpoints["total"] >= 3

    components = await tools.extract_components(analysis_id)
    assert components["ok"] is True
    names = {item["name"] for item in components["components"]}
    assert "DocumentSerializer" in names

    generated = await tools.generate_openapi(
        analysis_id=analysis_id,
        output_dir=str(tmp_path / "out"),
        engine="static",
    )
    assert generated["ok"] is True
    spec_path = Path(generated["result"]["spec_path"])
    assert spec_path.exists()

    validated = await tools.validate_openapi(spec_path=str(spec_path))
    assert validated["ok"] is True
    assert validated["report"]["valid"] is True

    markdown = await tools.generate_markdown(
        spec_path=str(spec_path), output_dir=str(tmp_path / "docs")
    )
    assert markdown["ok"] is True
    assert Path(markdown["result"]["index_path"]).exists()

    listed = await tools.list_analyses()
    assert listed["ok"] is True
    assert listed["count"] >= 1

    deleted = await tools.delete_analysis(analysis_id)
    assert deleted["ok"] is True
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_generate_docs_one_shot(django_project: Path, isolated_settings, tmp_path: Path) -> None:
    result = await tools.generate_docs(
        str(django_project),
        output_dir=str(tmp_path / "bundle"),
        engine="static",
        force=True,
    )
    assert result["ok"] is True
    assert Path(result["openapi"]["spec_path"]).exists()
    assert Path(result["markdown"]["index_path"]).exists()


@pytest.mark.asyncio
async def test_tool_errors_are_structured(isolated_settings) -> None:
    result = await tools.extract_endpoints("not-a-valid-id")
    assert result["ok"] is False
    assert "error" in result
