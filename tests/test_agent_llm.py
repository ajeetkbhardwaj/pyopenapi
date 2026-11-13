from __future__ import annotations

import httpx
import pytest

from pyopenapi.core.agent_llm import AgentLlmError, validate_payload
from pyopenapi.core.llm import ProviderConfig
from pyopenapi.core.llm_adapters import OpenAICompatibleAgentClient
from pyopenapi.core.tracing import TraceRecorder


def _response(content: str, *, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": usage or {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        },
    )


def _client(handler, *, max_retries: int = 2) -> OpenAICompatibleAgentClient:
    provider = ProviderConfig(
        name="openai-compatible",
        base_url="http://test/v1",
        spec_model="test-model",
    )
    return OpenAICompatibleAgentClient(
        provider,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    )


async def test_openai_compatible_client_returns_validated_json() -> None:
    recorder = TraceRecorder(provider="openai-compatible", model="test-model")
    client = _client(lambda request: _response('{"summary": "ok", "confidence": 0.9}'))

    output = await client.reason(
        system_prompt="system",
        prompt="user",
        schema={"type": "object", "required": ["summary"]},
        trace_recorder=recorder,
        stage="reason_endpoints",
        item_id="endpoint-1",
        prompt_version="v1",
    )

    assert output.data == {"summary": "ok", "confidence": 0.9}
    assert output.provider == "openai-compatible"
    assert output.model == "test-model"
    assert output.attempts == 1
    assert output.usage["total_tokens"] == 12
    assert recorder.trace.attempts[0].valid is True
    assert recorder.trace.attempts[0].total_tokens == 12
    assert recorder.trace.attempts[0].confidence == 0.9


async def test_openai_compatible_client_retries_invalid_json() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return _response("not json")
        return _response('{"summary": "recovered"}')

    client = _client(handler, max_retries=1)
    output = await client.reason(
        system_prompt="system",
        prompt="user",
        schema={"type": "object", "required": ["summary"]},
    )

    assert calls["count"] == 2
    assert output.attempts == 2
    assert output.data["summary"] == "recovered"


async def test_openai_compatible_client_rejects_schema_mismatch() -> None:
    client = _client(lambda request: _response('{"description": "missing summary"}'), max_retries=0)

    with pytest.raises(AgentLlmError, match="failed validation"):
        await client.reason(
            system_prompt="system",
            prompt="user",
            schema={"type": "object", "required": ["summary"]},
        )


def test_validate_payload_reports_missing_keys() -> None:
    assert validate_payload("nope", None) == ["response is not a JSON object"]
    assert validate_payload({}, {"type": "object", "required": ["a", "b"]}) == [
        "missing required key: a",
        "missing required key: b",
    ]
