from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pyopenapi.core import llm
from pyopenapi.core.spec import generate_openapi_sync


def _sample_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Demo API", "version": "1.0.0"},
        "paths": {
            "/api/documents/": {
                "get": {
                    "operationId": "get_api_documents",
                    "summary": "DocumentViewSet.list",
                    "tags": ["api"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/DocumentSerializer"},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "DocumentSerializer": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                }
            }
        },
    }


@pytest.fixture
def fake_nvidia(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-real")
    monkeypatch.setenv("PYOPENAPI_LLM_PROVIDER", "nvidia")

    async def fake_post(client, provider, *, model, prompt, max_tokens=1800):
        if "Write a 2-3 sentence overview" in prompt:
            return '{"description": "A demo API for documents."}'
        return json.dumps(
            {
                "operations": {
                    "GET /api/documents/": {
                        "summary": "List documents",
                        "description": "Returns all documents.",
                        "tags": ["documents"],
                    }
                },
                "components": {
                    "DocumentSerializer": {
                        "description": "A document payload.",
                        "fields": {"title": {"description": "Human readable title."}},
                    }
                },
            }
        )

    monkeypatch.setattr(llm, "_post_chat", fake_post)


@pytest.mark.asyncio
async def test_enrichment_merges_documentation(fake_nvidia) -> None:
    spec, warnings = await llm.enrich_openapi_with_nvidia(_sample_spec())

    operation = spec["paths"]["/api/documents/"]["get"]
    assert operation["summary"] == "List documents"
    assert operation["description"] == "Returns all documents."
    assert operation["tags"] == ["documents"]

    schema = spec["components"]["schemas"]["DocumentSerializer"]
    assert schema["description"] == "A document payload."
    assert schema["properties"]["title"]["description"] == "Human readable title."
    assert spec["info"]["description"] == "A demo API for documents."
    assert any("nvidia enrichment applied" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_enrichment_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYOPENAPI_LLM_PROVIDER", "openai-compatible")
    monkeypatch.delenv("PYOPENAPI_LLM_BASE_URL", raising=False)
    spec, warnings = await llm.enrich_openapi_with_llm(_sample_spec())
    assert any("no provider configured" in warning for warning in warnings)
    assert spec["paths"]["/api/documents/"]["get"]["summary"] == "DocumentViewSet.list"


def test_resolve_provider_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("PYOPENAPI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PYOPENAPI_LLM_SPEC_MODEL", "llama3.1")
    provider = llm.resolve_provider()
    assert provider is not None
    assert provider.name == "ollama"
    assert provider.base_url.endswith("/v1")
    assert provider.spec_model == "llama3.1"
    assert provider.requires_key is False


def test_auto_selects_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "nvidia_api_key", lambda: None)
    monkeypatch.setattr(llm, "openrouter_api_key", lambda: None)
    monkeypatch.delenv("PYOPENAPI_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    provider = llm.resolve_provider()
    assert provider is not None
    assert provider.name == "ollama"


def test_resolve_provider_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("PYOPENAPI_LLM_PROVIDER", "openrouter")
    provider = llm.resolve_provider()
    assert provider is not None
    assert provider.name == "openrouter"
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.requires_key is True
    assert provider.spec_model == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_auto_selects_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "nvidia_api_key", lambda: None)
    monkeypatch.setattr(llm, "openrouter_api_key", lambda: "test-openrouter-key")
    monkeypatch.delenv("PYOPENAPI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    provider = llm.resolve_provider()
    assert provider is not None
    assert provider.name == "openrouter"


@pytest.mark.asyncio
async def test_discover_model_skips_embeddings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "nomic-embed-text"}, {"id": "llama3.1"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = llm.ProviderConfig(name="ollama", base_url="http://127.0.0.1:11434/v1")
        assert await llm._discover_model(client, provider) == "llama3.1"


@pytest.mark.asyncio
async def test_enrichment_with_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("PYOPENAPI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PYOPENAPI_LLM_SPEC_MODEL", "llama3.1")

    async def fake_post(client, provider, *, model, prompt, max_tokens=1800):
        if "Write a 2-3 sentence overview" in prompt:
            return '{"description": "Local overview."}'
        return json.dumps(
            {"operations": {"GET /api/documents/": {"summary": "Local summary"}}}
        )

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    spec, warnings = await llm.enrich_openapi_with_llm(_sample_spec(), provider_name="ollama")
    assert spec["paths"]["/api/documents/"]["get"]["summary"] == "Local summary"
    assert spec["info"]["description"] == "Local overview."
    assert any("ollama enrichment applied" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_enrichment_failure_keeps_static_spec(fake_nvidia, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_post(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm, "_post_chat", failing_post)
    spec, warnings = await llm.enrich_openapi_with_nvidia(_sample_spec())
    assert spec["paths"]["/api/documents/"]["get"]["summary"] == "DocumentViewSet.list"
    assert any("kept the static spec" in warning or "no usable changes" in warning for warning in warnings)


def test_generate_openapi_with_nvidia_engine(
    django_project: Path, isolated_settings, fake_nvidia, tmp_path: Path
) -> None:
    from pyopenapi.core.analysis import AnalysisManager

    record = AnalysisManager().analyze(django_project, "auto", use_dynamic=False, force=True)
    result = generate_openapi_sync(record, tmp_path / "out", engine="nvidia")

    spec = __import__("yaml").safe_load(Path(result.spec_path).read_text(encoding="utf-8"))
    operation = spec["paths"]["/api/documents/"]["get"]
    assert operation["summary"] == "List documents"
    assert spec["info"]["description"] == "A demo API for documents."
    assert any("nvidia enrichment applied" in warning for warning in result.warnings)
