from __future__ import annotations

import asyncio
import concurrent.futures
import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import RouterConfig
from .router import MiniRouter
from .types import RouteRequest

_TASK_HINTS = {
    "coding": ("code", "function", "bug", "refactor", "python", "javascript", "typescript", "class ", "def ", "sql"),
    "agents": ("agent", "tool", "plan", "steps", "workflow", "orchestrate"),
    "reasoning": ("reason", "prove", "math", "derive", "step by step", "why"),
    "vision": ("image", "vision", "screenshot", "diagram"),
    "translation": ("translate", "translation"),
    "safety": ("safe", "moderation", "toxicity", "policy"),
}

AUTO_ALIASES = {"", "router", "router-auto", "nvidia-auto", "auto", "nvidia-fast"}


def _infer_task_type(messages: list[dict[str, Any]], explicit: str | None = None) -> str:
    if explicit and explicit not in AUTO_ALIASES:
        return explicit
    text = " ".join((m.get("content") or "") if isinstance(m.get("content"), str) else "" for m in messages if m.get("role") == "user").lower()
    for task, hints in _TASK_HINTS.items():
        if any(h in text for h in hints):
            return task
    return "general"


def _messages_to_prompt(messages: list[dict[str, Any]]) -> tuple[str | None, str]:
    system_parts = [m.get("content", "") for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str)]
    convo = []
    for m in messages:
        if m.get("role") == "system":
            continue
        role = m.get("role", "user")
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        convo.append(f"{role}: {content}")
    system = "\n".join(p for p in system_parts if p) or None
    prompt = "\n".join(convo).strip() or "Hello"
    return system, prompt


def _normalize_stream_chunk(chunk: dict[str, Any], model_id: str) -> dict[str, Any]:
    out = dict(chunk)
    out.setdefault("object", "chat.completion.chunk")
    out["model"] = model_id
    choices = out.get("choices")
    if isinstance(choices, list):
        for c in choices:
            if not isinstance(c, dict):
                continue
            delta = c.get("delta")
            if isinstance(delta, dict):
                if "reasoning_content" not in delta:
                    for k in ("reasoning", "thinking"):
                        v = delta.get(k)
                        if isinstance(v, str) and v:
                            delta["reasoning_content"] = v
                            break
            message = c.get("message")
            if isinstance(message, dict):
                if "reasoning_content" not in message:
                    for k in ("reasoning", "thinking"):
                        v = message.get(k)
                        if isinstance(v, str) and v:
                            message["reasoning_content"] = v
                            break
    return out


class RouterHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler_cls, router: MiniRouter, loop: asyncio.AbstractEventLoop):
        super().__init__(addr, handler_cls)
        self.router = router
        self.loop = loop

    def route_timeout(self) -> float:
        cfg = self.router.config
        attempts = max(1, int(cfg.max_fallback_attempts or 3))
        return max(30.0, float(cfg.first_token_timeout_s or 12.0) * attempts + 15.0)


class Handler(BaseHTTPRequestHandler):
    server: RouterHTTPServer

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == "/health":
            return self._send_json(200, {"status": "ok", "service": "router", "free_only": True, "groups": ["nvidia-auto", "nvidia-fast"], "streaming": True, "thinking": True})
        if self.path == "/v1/models":
            models = self.server.router.list_models()
            data = [
                {"id": m.id, "object": "model", "owned_by": m.owned_by or "nvidia", "endpoint": m.endpoint, "usage": m.usage}
                for m in models
            ]
            data.insert(0, {"id": "nvidia-fast", "object": "model", "owned_by": "router", "endpoint": "chat", "usage": ["chat", "fast"], "description": "Auto-route across fast/low-latency chat models"})
            data.insert(0, {"id": "nvidia-auto", "object": "model", "owned_by": "router", "endpoint": "chat", "usage": ["chat", "auto"], "description": "Auto-route across all chat-capable models"})
            return self._send_json(200, {"object": "list", "data": data})
        return self._send_json(404, {"error": {"message": "Not found", "type": "not_found"}})

    def _build_request(self, body: dict[str, Any]):
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return None, (400, {"error": {"message": "messages[] is required", "type": "invalid_request_error"}})
        requested_model = body.get("model") if isinstance(body.get("model"), str) else "nvidia-auto"
        task_type = _infer_task_type(messages, body.get("task_type"))
        override = requested_model or "nvidia-auto"
        system, prompt = _messages_to_prompt(messages)
        disable_thinking = body.get("disable_thinking")
        if disable_thinking is None and "thinking" in body:
            # allow {"thinking": false} or {"thinking": {"type":"disabled"}}
            tv = body.get("thinking")
            if isinstance(tv, bool):
                disable_thinking = (tv is False)
            elif isinstance(tv, dict):
                ttype = str(tv.get("type", "")).lower()
                if ttype in {"disabled", "off", "none"}:
                    disable_thinking = True
        tools = body.get("tools") if isinstance(body.get("tools"), list) else None
        req = RouteRequest(
            prompt=prompt,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=body.get("tool_choice"),
            task_type=task_type,
            temperature=float(body.get("temperature", 0.0) or 0.0),
            max_tokens=int(body.get("max_tokens", 1024) or 1024),
            json_mode=bool(body.get("response_format", {}).get("type") == "json_object") if isinstance(body.get("response_format"), dict) else False,
            disable_thinking=disable_thinking,
            model_override=override,
            cost_mode="free_only",
        )
        return (req, task_type, override), None

    def _handle_stream(self, req: RouteRequest):
        raw_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        async def _produce() -> None:
            try:
                model_id, chunk_iter, _attempts = await self.server.router.route_stream(req)
                raw_queue.put(("model", model_id))
                async for chunk in chunk_iter:
                    raw_queue.put(("chunk", chunk))
                raw_queue.put(("done", None))
            except Exception as exc:
                raw_queue.put(("error", str(exc)))

        # The router and its httpx client live on a single persistent loop; driving
        # coroutines via run_coroutine_threadsafe keeps loop affinity intact.
        asyncio.run_coroutine_threadsafe(_produce(), self.server.loop)

        try:
            kind, value = raw_queue.get(timeout=self.server.route_timeout())
        except queue.Empty:
            return self._send_json(504, {"error": {"message": "router timed out", "type": "router_timeout"}})

        if kind == "error":
            return self._send_json(502, {"error": {"message": value, "type": "router_error"}})

        model_id = str(value) if kind == "model" else ""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Streams have no Content-Length or chunked framing; closing after
        # [DONE] is what signals end-of-body to the client.
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def _emit(chunk: dict[str, Any]) -> None:
            out = _normalize_stream_chunk(chunk, model_id)
            self.wfile.write(f"data: {json.dumps(out, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        idle_timeout = max(60.0, float(self.server.router.config.timeout_s) * 2.0)
        try:
            if kind == "chunk":
                _emit(value)
            while True:
                try:
                    kind, value = raw_queue.get(timeout=idle_timeout)
                except queue.Empty:
                    break
                if kind == "chunk":
                    _emit(value)
                elif kind == "error":
                    err = {"error": {"message": value, "type": "router_error"}}
                    self.wfile.write(f"data: {json.dumps(err)}\n\n".encode())
                    self.wfile.flush()
                    break
                elif kind == "done":
                    break
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._send_json(404, {"error": {"message": "Not found", "type": "not_found"}})
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send_json(400, {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}})

        built, err = self._build_request(body)
        if err:
            return self._send_json(*err)
        req, task_type, override = built

        if body.get("stream") is True:
            return self._handle_stream(req)

        future = asyncio.run_coroutine_threadsafe(self.server.router.route(req), self.server.loop)
        try:
            result = future.result(timeout=self.server.route_timeout())
        except concurrent.futures.TimeoutError:
            future.cancel()
            return self._send_json(504, {"error": {"message": "router timed out", "type": "router_timeout"}})
        except Exception as e:
            return self._send_json(502, {"error": {"message": str(e), "type": "router_error"}})

        message: dict[str, Any] = {"role": "assistant", "content": result.content}
        if result.reasoning:
            message["reasoning_content"] = result.reasoning
        raw_usage = result.raw.get("usage", {}) if isinstance(result.raw, dict) else {}
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": result.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": int(raw_usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(raw_usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(raw_usage.get("total_tokens", 0) or 0),
            },
            "router": {"task_type": task_type, "pool": override if override in {"nvidia-auto", "nvidia-fast", "router-auto", "auto"} else "specific", "attempts": len(result.attempts)},
        }
        return self._send_json(200, payload)


def serve(config: RouterConfig, host: str = "127.0.0.1", port: int = 8000) -> None:
    loop = asyncio.new_event_loop()
    holder: dict[str, Any] = {}
    ready = threading.Event()

    def _loop_thread() -> None:
        asyncio.set_event_loop(loop)
        try:
            router = MiniRouter(config)
            router.refresh()
            if config.verify_models:
                loop.run_until_complete(router.verify_models())
            holder["router"] = router
        except Exception as exc:
            holder["error"] = exc
        ready.set()
        if "error" not in holder:
            loop.run_forever()

    thread = threading.Thread(target=_loop_thread, daemon=True, name="mini-router-loop")
    thread.start()
    ready.wait()

    if "error" in holder:
        raise holder["error"]

    httpd = RouterHTTPServer((host, port), Handler, holder["router"], loop)
    print(f"router service listening on http://{host}:{port} (groups: nvidia-auto, nvidia-fast | streaming+thinking supported | POST /v1/chat/completions, GET /v1/models, GET /health)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        thread.join(timeout=2.0)
