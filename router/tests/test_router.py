from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from router.cache import ResponseCache
from router.client import NvidiaClient
from router.config import RouterConfig
from router.router import MiniRouter
from router.telemetry import TelemetryStore
from router.types import RouteRequest, TelemetryRecord


def _config(tmp_path: Path) -> RouterConfig:
    return RouterConfig(
        api_key="test-key",
        telemetry_path=tmp_path / "telemetry.jsonl",
        cache_path=tmp_path / "cache.json",
        max_fallback_attempts=5,
        first_token_timeout_s=2.0,
    )


def _make_router(tmp_path: Path, client) -> MiniRouter:
    router = MiniRouter(_config(tmp_path))
    router.refresh()
    router.client = client
    return router


def _chat_ids(router: MiniRouter) -> list[str]:
    return [m.id for m in router.list_models("nvidia-auto") if m.endpoint == "chat"]


class FakeStreamClient:
    """Async streaming client that returns empty streams for `empty_models`."""

    def __init__(self, empty_models: set[str]) -> None:
        self.empty_models = empty_models
        self.calls: list[str] = []

    async def chat_completions_stream(self, payload, task_type="general"):
        mid = payload["model"]
        self.calls.append(mid)
        if mid in self.empty_models:
            # clean but content-less stream (the historical "agent stalls" bug)
            yield None, TelemetryRecord(model_id=mid, endpoint="test", task_type=task_type, success=True)
            return
        yield {"choices": [{"delta": {"content": "hello"}}]}, None
        yield None, TelemetryRecord(model_id=mid, endpoint="test", task_type=task_type, success=True)


class FakeClient:
    def __init__(self, empty_models: set[str]) -> None:
        self.empty_models = empty_models
        self.calls: list[str] = []

    async def chat_completions(self, payload, task_type="general"):
        mid = payload["model"]
        self.calls.append(mid)
        rec = TelemetryRecord(model_id=mid, endpoint="test", task_type=task_type, success=True)
        if mid in self.empty_models:
            return {"choices": [{"message": {"content": ""}}]}, rec
        return {"choices": [{"message": {"content": "hello"}}]}, rec


@pytest.mark.asyncio
async def test_stream_falls_back_on_empty_stream(tmp_path: Path) -> None:
    router = MiniRouter(_config(tmp_path))
    router.refresh()
    ids = _chat_ids(router)
    assert len(ids) >= 2, "registry should provide multiple chat models"

    client = FakeStreamClient(empty_models={ids[0]})
    router.client = client

    req = RouteRequest(prompt="hi", model_override="nvidia-auto")
    model_id, chunk_iter, _attempts = await router.route_stream(req)

    assert model_id != ids[0], "router must not select a model that produced an empty stream"
    chunks = [chunk async for chunk in chunk_iter]
    assert any(
        (chunk.get("choices") or [{}])[0].get("delta", {}).get("content") == "hello"
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_nonstream_falls_back_on_empty_response(tmp_path: Path) -> None:
    router = MiniRouter(_config(tmp_path))
    router.refresh()
    ids = _chat_ids(router)

    client = FakeClient(empty_models={ids[0]})
    router.client = client

    req = RouteRequest(prompt="hi", model_override="nvidia-auto")
    result = await router.route(req)

    assert result.model_id != ids[0]
    assert result.content == "hello"


@pytest.mark.asyncio
async def test_unknown_model_raises_actionable_error(tmp_path: Path) -> None:
    router = MiniRouter(_config(tmp_path))
    router.refresh()
    req = RouteRequest(prompt="hi", model_override="totally-not-a-model")
    with pytest.raises(RuntimeError) as exc:
        await router.route(req)
    assert "nvidia-auto" in str(exc.value) or "nvidia-fast" in str(exc.value)


@pytest.mark.asyncio
async def test_stream_all_empty_raises(tmp_path: Path) -> None:
    router = MiniRouter(_config(tmp_path))
    router.refresh()
    ids = _chat_ids(router)
    client = FakeStreamClient(empty_models=set(ids))
    router.client = client

    req = RouteRequest(prompt="hi", model_override="nvidia-auto")
    with pytest.raises(RuntimeError):
        await router.route_stream(req)


@pytest.mark.asyncio
async def test_stream_client_yields_failure_record_on_http_error(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = NvidiaClient(config)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, text="bad tools"))
    )
    items = [
        item
        async for item in client.chat_completions_stream(
            {"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
            task_type="coding",
        )
    ]
    assert items, "client should always yield a terminal record"
    chunk, rec = items[-1]
    assert chunk is None
    assert rec is not None and rec.success is False
    assert rec.error_type and rec.error_type.startswith("http_400")


@pytest.mark.asyncio
async def test_verify_models_drops_unavailable_entries(tmp_path: Path) -> None:
    class ProbeClient:
        async def list_models(self):
            return {"only-model"}

    router = MiniRouter(replace(_config(tmp_path), verify_models=True))
    router.refresh()
    router.client = ProbeClient()
    removed = await router.verify_models()

    assert removed
    assert set(router.registry.models) <= {"only-model"}

    idle = MiniRouter(_config(tmp_path))
    idle.refresh()
    assert await idle.verify_models() == []


def test_cache_concurrent_access(tmp_path: Path) -> None:
    import threading
    cache = ResponseCache(tmp_path / "cache.json", ttl_s=60)
    errors = []

    def writer(i: int) -> None:
        try:
            cache.set(f"key-{i}", {"v": i})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for i in range(50):
        assert cache.get(f"key-{i}") == {"v": i}


def test_telemetry_concurrent_append(tmp_path: Path) -> None:
    import threading
    store = TelemetryStore(tmp_path / "telemetry.jsonl")
    errors = []

    def appender(i: int) -> None:
        try:
            store.append(TelemetryRecord(
                model_id=f"model-{i % 5}",
                endpoint="test",
                task_type="general",
                success=True,
                latency_ms=10.0 * i,
            ))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=appender, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    summary = store.summarize()
    assert len(summary) == 5
    for agg in summary.values():
        assert agg["count"] == 20
