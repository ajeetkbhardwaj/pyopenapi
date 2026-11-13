from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import RouterConfig
from .types import TelemetryRecord


def extract_message_reasoning(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    for key in ("reasoning_content", "reasoning", "thinking"):
        val = message.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


class NvidiaClient:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self._headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = httpx.Timeout(
            connect=self.config.connect_timeout_s,
            read=self.config.timeout_s,
            write=self.config.timeout_s,
            pool=self.config.connect_timeout_s,
        )
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        self._client = httpx.AsyncClient(timeout=self._timeout, limits=limits)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def list_models(self) -> set[str]:
        """Return model ids exposed by the upstream ``/models`` endpoint."""

        url = f"{self.config.base_url}/models"
        resp = await self._client.get(url, headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return set()
        return {
            str(item.get("id"))
            for item in items
            if isinstance(item, dict) and item.get("id")
        }

    async def chat_completions(self, payload: dict[str, Any], task_type: str = "general") -> tuple[dict[str, Any], TelemetryRecord]:
        url = f"{self.config.base_url}/chat/completions"
        start = time.perf_counter()
        rec = TelemetryRecord(model_id=payload.get("model", ""), endpoint=url, task_type=task_type)
        try:
            resp = await self._client.post(url, headers=self._headers, json=payload)
            rec.latency_ms = (time.perf_counter() - start) * 1000
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage") or {}
            rec.output_tokens = int(usage.get("completion_tokens") or 0)
            rec.context_tokens = int(usage.get("prompt_tokens") or 0)
            rec.success = True
            return data, rec
        except httpx.HTTPStatusError as e:
            rec.latency_ms = (time.perf_counter() - start) * 1000
            rec.success = False
            detail = ""
            try:
                body = e.response.text.strip().replace("\n", " ")
                if body:
                    detail = ": " + body[:240]
            except Exception:
                detail = ""
            rec.error_type = f"http_{e.response.status_code}{detail}"
            raise RuntimeError(f"http_{e.response.status_code}{detail}") from e
        except Exception as e:
            rec.latency_ms = (time.perf_counter() - start) * 1000
            rec.success = False
            rec.error_type = type(e).__name__
            raise

    async def chat_completions_stream(
        self, payload: dict[str, Any], task_type: str = "general"
    ) -> AsyncIterator[tuple[dict[str, Any] | None, TelemetryRecord | None]]:
        """Yield (chunk_dict, None) for each SSE JSON chunk, then (None, record) at end."""
        url = f"{self.config.base_url}/chat/completions"
        start = time.perf_counter()
        rec = TelemetryRecord(model_id=payload.get("model", ""), endpoint=url, task_type=task_type)
        stream_payload = {**payload, "stream": True}
        try:
            async with self._client.stream("POST", url, headers=self._headers, json=stream_payload) as resp:
                # capture connect/header latency; content streams after
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                    else:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except Exception:
                        continue
                    usage = chunk.get("usage") or {}
                    if usage:
                        rec.output_tokens = int(usage.get("completion_tokens") or rec.output_tokens or 0)
                        rec.context_tokens = int(usage.get("prompt_tokens") or rec.context_tokens or 0)
                    yield chunk, None
            rec.success = True
            rec.latency_ms = (time.perf_counter() - start) * 1000
        except httpx.HTTPStatusError as e:
            rec.success = False
            rec.latency_ms = (time.perf_counter() - start) * 1000
            detail = ""
            try:
                body = e.response.text.strip().replace("\n", " ")
                if body:
                    detail = ": " + body[:240]
            except Exception:
                detail = ""
            rec.error_type = f"http_{e.response.status_code}{detail}"
            # Surface the failure as a terminal record so the router can fall back
            # cleanly instead of re-raising mid-generator.
            yield None, rec
            return
        except Exception as e:
            rec.success = False
            rec.latency_ms = (time.perf_counter() - start) * 1000
            rec.error_type = type(e).__name__
            yield None, rec
            return
        yield None, rec
