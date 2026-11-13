from __future__ import annotations

from pathlib import Path

import pytest

from pyopenapi.core.analysis import AnalysisRecord
from pyopenapi.core.spec import generate_openapi
from pyopenapi.models import AnalysisSummary


def _record(framework: str = "flask") -> AnalysisRecord:
    endpoints = [
        {
            "method": "GET",
            "url": {"url": "/items/"},
            "view": "ItemView",
            "function": "list_items",
        }
    ]
    summary = AnalysisSummary(
        analysis_id="abcdef0123456789",
        project_path="/tmp/demo",
        framework=framework,
        endpoint_count=len(endpoints),
        component_count=0,
        created_at="2026-01-01T00:00:00+00:00",
        duration_ms=1,
        cache_dir="/tmp/cache",
        output_dir="/tmp/cache/analyses/abcdef0123456789",
    )
    return AnalysisRecord(summary=summary, endpoints=endpoints, components={}, metadata={})


@pytest.mark.asyncio
async def test_auto_without_provider_falls_back_to_static(
    tmp_path: Path, monkeypatch, isolated_settings
) -> None:
    monkeypatch.setattr("pyopenapi.core.agent_llm.agent_llm_available", lambda **kwargs: False)
    monkeypatch.setattr("pyopenapi.core.spec.legacy_llm_available", lambda: False)

    result = await generate_openapi(_record(), tmp_path, engine="auto")

    assert Path(result.spec_path).is_file()
    assert result.endpoint_count >= 1
    assert any("deterministic static" in message for message in result.warnings)


@pytest.mark.asyncio
async def test_explicit_static_needs_no_provider(
    tmp_path: Path, monkeypatch, isolated_settings
) -> None:
    monkeypatch.setattr("pyopenapi.core.agent_llm.agent_llm_available", lambda **kwargs: False)

    result = await generate_openapi(_record(), tmp_path, engine="static")

    assert Path(result.spec_path).is_file()
    assert result.endpoint_count >= 1
    assert not any("deterministic static" in message for message in result.warnings)
