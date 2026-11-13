from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from router.server import Handler, RouterHTTPServer


class FakeRouter:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            max_fallback_attempts=2,
            first_token_timeout_s=1.0,
            timeout_s=5.0,
        )
        self.last_request = None
        self.last_loop = None

    def list_models(self, pool=None):
        return []

    def _check_loop(self) -> None:
        running = asyncio.get_running_loop()
        if self.last_loop is None:
            self.last_loop = running
        assert running is self.last_loop, "router coroutine ran on a foreign event loop"

    async def route(self, req):
        self.last_request = req
        self._check_loop()
        return SimpleNamespace(
            model_id="fake-model",
            content="hello",
            reasoning="thinking",
            raw={"usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
            attempts=[],
        )

    async def route_stream(self, req):
        self.last_request = req

        async def _gen():
            self._check_loop()
            yield {"choices": [{"delta": {"content": "he"}}]}
            yield {"choices": [{"delta": {"reasoning": "hm", "content": "llo"}}]}

        return "fake-model", _gen(), []


@pytest.fixture()
def server():
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    ready.wait()

    router = FakeRouter()
    httpd = RouterHTTPServer(("127.0.0.1", 0), Handler, router, loop)
    port = httpd.server_address[1]
    st = threading.Thread(target=httpd.serve_forever, daemon=True)
    st.start()
    try:
        yield router, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2.0)


def test_nonstream_completion(server) -> None:
    router, base = server
    resp = httpx.post(
        f"{base}/v1/chat/completions",
        json={"model": "nvidia-auto", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["choices"][0]["message"]["reasoning_content"] == "thinking"
    assert router.last_request.messages[0]["content"] == "hi"


def test_stream_completion(server) -> None:
    _router, base = server
    lines: list[str] = []
    with httpx.stream(
        "POST",
        f"{base}/v1/chat/completions",
        json={"model": "nvidia-auto", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        timeout=10,
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line:
                lines.append(line)

    payloads = [line[len("data: "):] for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert lines[-1] == "data: [DONE]"
    assert any('"content": "he"' in p for p in payloads)
    assert any('"reasoning_content": "hm"' in p for p in payloads)


def test_request_with_tools_and_messages_passthrough(server) -> None:
    router, base = server
    tools = [{"type": "function", "function": {"name": "shell", "parameters": {}}}]
    resp = httpx.post(
        f"{base}/v1/chat/completions",
        json={
            "model": "nvidia-auto",
            "messages": [{"role": "user", "content": "run ls"}],
            "tools": tools,
        },
        timeout=10,
    )
    assert resp.status_code == 200
    assert router.last_request.tools == tools
    assert router.last_request.messages[0]["content"] == "run ls"


def test_specific_model_does_not_pollute_task_type(server) -> None:
    router, base = server
    resp = httpx.post(
        f"{base}/v1/chat/completions",
        json={
            "model": "nvidia/llama-3.1-nemotron-70b-instruct",
            "messages": [{"role": "user", "content": "fix this python bug"}],
        },
        timeout=10,
    )
    assert resp.status_code == 200
    assert router.last_request.task_type == "coding"
    assert router.last_request.model_override == "nvidia/llama-3.1-nemotron-70b-instruct"
