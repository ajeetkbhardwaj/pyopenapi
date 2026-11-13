from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from .cache import ResponseCache
from .client import NvidiaClient, extract_message_reasoning
from .config import RouterConfig, load_config
from .registry import ModelRegistry
from .scoring import rank_models
from .telemetry import TelemetryStore
from .types import RouteRequest, RouteResult, TelemetryRecord

logger = logging.getLogger(__name__)

CHAT_ENDPOINT = "chat"


class MiniRouter:
    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config = config or load_config()
        self.client = NvidiaClient(self.config)
        self.registry = ModelRegistry(
            allowed_models=set(self.config.allowed_models),
            blocked_models=set(self.config.blocked_models),
        )
        self.telemetry = TelemetryStore(self.config.telemetry_path)
        self.cache = ResponseCache(self.config.cache_path, ttl_s=self.config.cache_ttl_s)

    async def aclose(self) -> None:
        await self.client.aclose()

    def refresh(self) -> None:
        self.registry.load()
        self.registry.telemetry = self.telemetry.summarize()

    async def verify_models(self) -> list[str]:
        """Probe ``/models`` and drop registry entries no longer offered."""

        if not self.config.verify_models or not self.config.api_key:
            return []
        try:
            available = await self.client.list_models()
        except Exception as exc:
            logger.warning("Model registry probe failed: %s", exc)
            return []
        removed = self.registry.retain(available)
        if removed:
            logger.warning("Dropped %d unavailable model(s): %s", len(removed), ", ".join(removed))
        return removed

    def list_models(self, category: str | None = None):
        if not self.registry.models:
            self.refresh()
        if category:
            group = self.registry.resolve_group(category)
            if group:
                models = self.registry.pool(group)
            else:
                cat = category.lower()
                models = [m for m in self.registry.models.values() if cat in [u.lower() for u in m.usage] or cat == m.endpoint]
        else:
            models = list(self.registry.models.values())
        return sorted(models, key=lambda m: m.id)

    def rank(self, req: RouteRequest) -> list[tuple[Any, float]]:
        if not self.registry.models:
            self.refresh()
        return rank_models(self.registry, req)

    def _ranked_chat_models(self, req: RouteRequest):
        group = self.registry.resolve_group(req.model_override)
        if req.model_override and not group:
            m = self.registry.get(req.model_override)
            if not m:
                chat_ids = sorted(
                    model.id
                    for model in self.registry.pool("nvidia-auto")
                    if model.endpoint == CHAT_ENDPOINT
                )
                preview = ", ".join(chat_ids[:8])
                raise RuntimeError(
                    f"Model '{req.model_override}' is not in the curated free-endpoint "
                    f"registry. Use 'nvidia-auto' or 'nvidia-fast', or one of: {preview}"
                )
            if m.endpoint != CHAT_ENDPOINT:
                raise RuntimeError(
                    f"Model '{req.model_override}' endpoint '{m.endpoint}' is not "
                    "chat-completion; use a native endpoint for it"
                )
            return [(m, 1.0)], group
        ranked = self.rank(req)
        # limit attempts for latency (interactive/fast routing)
        limit = max(1, int(self.config.max_fallback_attempts or 3))
        return ranked[:limit], group

    def _build_payload(self, model_id: str, req: RouteRequest, *, include_tools: bool = True, include_json_mode: bool = True) -> dict[str, Any]:
        if req.messages:
            messages = list(req.messages)
            if req.system and not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {"role": "system", "content": req.system})
        else:
            messages = []
            if req.system:
                messages.append({"role": "system", "content": req.system})
            messages.append({"role": "user", "content": req.prompt or "Hello"})

        max_tokens = max(1, int(req.max_tokens or 4096))
        # clamp by known model context when available (rough: reserve room for prompt)
        try:
            m = self.registry.get(model_id)
            if m and m.context_limit:
                reserve = max(256, int(req.context_tokens or 0))
                max_out = max(256, int(m.context_limit) - reserve)
                max_tokens = min(max_tokens, max_out)
        except Exception:
            pass

        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": max_tokens,
        }
        if include_tools and req.tools:
            payload["tools"] = req.tools
            if req.tool_choice is not None:
                payload["tool_choice"] = req.tool_choice
        if include_json_mode and req.json_mode:
            payload["response_format"] = {"type": "json_object"}
        # thinking/reasoning: only send disable hints when explicitly requested.
        disable_thinking = req.disable_thinking
        if disable_thinking is True:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            payload["reasoning_effort"] = "low"
        return payload

    async def route(self, req: RouteRequest, allow_fallback: bool = True) -> RouteResult:
        if req.cost_mode != "free_only":
            raise RuntimeError("router enforces free_only routing")
        if not self.config.api_key:
            raise RuntimeError("NVIDIA_API_KEY is required (set env var or .env)")
        if not self.registry.models:
            self.refresh()

        ranked, group = self._ranked_chat_models(req)
        if not ranked:
            pool = f" in pool '{group}'" if group else ""
            raise RuntimeError(f"No eligible free-endpoint chat models for this request{pool}")

        attempts: list[TelemetryRecord] = []
        last_err: Exception | None = None
        for i, (model, _score) in enumerate(ranked):
            if model.endpoint != CHAT_ENDPOINT:
                continue
            cache_key = None
            # avoid cache for agent/tool/structured requests
            if req.temperature == 0.0 and not req.tools and not req.messages:
                cache_key = self.cache.make_key(model.id, req.prompt, req.system, req.temperature, req.task_type)
                cached = self.cache.get(cache_key)
                if cached:
                    return RouteResult(
                        model_id=model.id,
                        content=cached.get("content", ""),
                        reasoning=cached.get("reasoning", "") or "",
                        raw=cached,
                        attempts=attempts,
                    )

            try_variants = [(True, True)]
            if req.tools:
                try_variants.append((False, True))  # retry without tools if unsupported
            for include_tools, include_json in try_variants:
                payload = self._build_payload(model.id, req, include_tools=include_tools, include_json_mode=include_json)
                try:
                    data, rec = await self.client.chat_completions(payload, task_type=req.task_type)
                    msg = data.get("choices", [{}])[0].get("message", {}) or {}
                    content = msg.get("content", "") or ""
                    reasoning = extract_message_reasoning(msg)
                    has_tool_calls = bool(msg.get("tool_calls"))
                    if not content.strip() and not reasoning.strip() and not has_tool_calls:
                        # Empty payloads should fall back, not terminate the run.
                        raise RuntimeError("empty_response")
                    rec.retry_count = i
                    attempts.append(rec)
                    self.telemetry.append(rec)
                    self.registry.telemetry = self.telemetry.summarize()
                    if cache_key and (content or reasoning) and not req.tools and not req.messages:
                        self.cache.set(cache_key, {"content": content, "reasoning": reasoning, "model": model.id, "raw": data})
                    return RouteResult(model_id=model.id, content=content, reasoning=reasoning, raw=data, attempts=attempts)
                except Exception as e:
                    err_text = str(e).lower()
                    rec = TelemetryRecord(
                        model_id=model.id,
                        endpoint=f"{self.config.base_url}/chat/completions",
                        task_type=req.task_type,
                        success=False,
                        error_type=str(e)[:180] or type(e).__name__,
                        retry_count=i,
                    )
                    attempts.append(rec)
                    self.telemetry.append(rec)
                    self.registry.telemetry = self.telemetry.summarize()
                    last_err = e
                    # if tools/json caused 400, try variant; else move to next model
                    recoverable = ("tool" in err_text or "function" in err_text or "response_format" in err_text or "unsupported" in err_text)
                    if include_tools and req.tools and recoverable and len(try_variants) > 1:
                        continue
                    break
            if not allow_fallback:
                break
        raise RuntimeError(f"All free-endpoint model attempts failed: {last_err}")

    async def route_stream(self, req: RouteRequest, allow_fallback: bool = True) -> tuple[str, AsyncIterator[dict[str, Any]], list[TelemetryRecord]]:
        """Select best model and return (model_id, chunk_iterator, attempts).

        Fallback is attempted if stream setup or first token exceeds first_token_timeout_s.
        For agent/tool requests, on recoverable 4xx (tools/params unsupported) retries without tools or falls back.
        """
        if req.cost_mode != "free_only":
            raise RuntimeError("router enforces free_only routing")
        if not self.config.api_key:
            raise RuntimeError("NVIDIA_API_KEY is required (set env var or .env)")
        if not self.registry.models:
            self.refresh()

        ranked, group = self._ranked_chat_models(req)
        if not ranked:
            pool = f" in pool '{group}'" if group else ""
            raise RuntimeError(f"No eligible free-endpoint chat models for this request{pool}")

        attempts: list[TelemetryRecord] = []
        last_err: Exception | None = None
        first_token_timeout = max(1.0, float(self.config.first_token_timeout_s or 12.0))

        for i, (model, _score) in enumerate(ranked):
            if model.endpoint != CHAT_ENDPOINT:
                continue

            try_variants = [(True, True)]
            if req.tools:
                try_variants.append((False, True))  # retry without tools if unsupported

            for include_tools, include_json in try_variants:
                payload = self._build_payload(model.id, req, include_tools=include_tools, include_json_mode=include_json)
                agen = self.client.chat_completions_stream(payload, task_type=req.task_type)
                first_chunk: dict[str, Any] | None = None
                rec_final: TelemetryRecord | None = None
                try:
                    # wait for first content chunk or terminal record within TTFT budget
                    while True:
                        try:
                            item = await asyncio.wait_for(
                                agen.__anext__(), timeout=first_token_timeout
                            )
                        except StopAsyncIteration:
                            # upstream ended without a usable chunk
                            break
                        chunk, rec = item
                        if chunk is not None and first_chunk is None:
                            first_chunk = chunk
                            break
                        if rec is not None:
                            rec_final = rec
                            break

                    if first_chunk is None:
                        # Empty/failed stream: always fall back instead of returning
                        # an empty successful response that stalls the agent.
                        reason = (
                            rec_final.error_type
                            if rec_final is not None and not rec_final.success
                            else "empty_stream"
                        )
                        if rec_final is not None:
                            rec_final.success = False
                            if not rec_final.error_type:
                                rec_final.error_type = reason
                        raise RuntimeError(reason or "empty_stream")

                    async def _combined_stream(first=first_chunk, rest=agen, m=model, idx=i):
                        rec_done: TelemetryRecord | None = None
                        try:
                            if first is not None:
                                yield first
                            async for chunk, rec in rest:
                                if chunk is not None:
                                    yield chunk
                                if rec is not None:
                                    rec_done = rec
                        finally:
                            if rec_done is None:
                                rec_done = TelemetryRecord(model_id=m.id, endpoint=f"{self.config.base_url}/chat/completions", task_type=req.task_type, success=False, error_type="stream_incomplete", retry_count=idx)
                            rec_done.retry_count = idx
                            attempts.append(rec_done)
                            self.telemetry.append(rec_done)
                            self.registry.telemetry = self.telemetry.summarize()

                    return model.id, _combined_stream(), attempts
                except Exception as e:
                    try:
                        await agen.aclose()
                    except Exception:
                        pass
                    err_name = "ttft_timeout" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
                    if rec_final is not None and rec_final not in attempts:
                        rec_final.retry_count = i
                        if not rec_final.error_type:
                            rec_final.error_type = err_name
                        attempts.append(rec_final)
                        self.telemetry.append(rec_final)
                        self.registry.telemetry = self.telemetry.summarize()
                    elif rec_final is None:
                        rec = TelemetryRecord(
                            model_id=model.id,
                            endpoint=f"{self.config.base_url}/chat/completions",
                            task_type=req.task_type,
                            success=False,
                            error_type=err_name,
                            retry_count=i,
                        )
                        attempts.append(rec)
                        self.telemetry.append(rec)
                        self.registry.telemetry = self.telemetry.summarize()
                    last_err = e
                    err_text = str(e).lower()
                    recoverable = ("tool" in err_text or "function" in err_text or "response_format" in err_text or "unsupported" in err_text)
                    if include_tools and req.tools and recoverable and len(try_variants) > 1:
                        # try next variant on same model (without tools)
                        continue
                    # move to next model
                    break

            if not allow_fallback:
                break
            continue
        raise RuntimeError(f"All free-endpoint model attempts failed: {last_err}")
