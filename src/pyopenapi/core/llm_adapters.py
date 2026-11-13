"""OpenAI-compatible agent LLM adapter.

Request/response handling intentionally mirrors ``core/llm.py`` so local
servers (LM Studio, llama.cpp, Ollama, NVIDIA NIM and custom gateways) behave
consistently between single-pass enrichment and agent reasoning.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from ..utils import Stopwatch
from .agent_llm import (
    AgentLlmError,
    AgentLlmOutput,
    record_attempt,
    validate_payload,
)
from .llm import ProviderConfig, _discover_model, _headers, _parse_json_object
from .tracing import TraceRecorder


def _usage(data: dict[str, Any]) -> dict[str, int]:
    raw = data.get("usage")
    if not isinstance(raw, dict):
        return {}
    return {
        key: int(raw[key])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(raw.get(key), int)
    }


class OpenAICompatibleAgentClient:
    provider_name = "openai-compatible"

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        model: str | None = None,
        max_retries: int = 2,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.provider_name = provider.name
        self.model = model
        self.max_retries = max(0, max_retries)
        self.timeout = timeout
        self.transport = transport

    async def _resolve_model(
        self, client: httpx.AsyncClient, override: str | None
    ) -> str:
        model = override or self.model or self.provider.spec_model
        if model:
            return model
        model = await _discover_model(client, self.provider)
        if not model:
            raise AgentLlmError(
                f"No model configured for provider '{self.provider.name}'.",
                hint="Set PYOPENAPI_LLM_SPEC_MODEL or start a local server with a model loaded.",
            )
        self.model = model
        return model

    async def _post(
        self,
        client: httpx.AsyncClient,
        *,
        model: str,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict[str, int], str]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.provider.name == "openrouter":
            try:
                floor = int(os.getenv("PYOPENAPI_OPENROUTER_MAX_TOKENS", "4096"))
            except ValueError:
                floor = 4096
            payload["max_tokens"] = max(max_tokens, floor)
            payload["reasoning"] = {
                "effort": os.getenv("PYOPENAPI_OPENROUTER_REASONING_EFFORT", "low")
            }
        elif self.provider.name == "nvidia":
            model_l = (model or "").lower()
            if "nemotron" in model_l:
                try:
                    floor = int(os.getenv("PYOPENAPI_NVIDIA_MAX_TOKENS", "4096"))
                except ValueError:
                    floor = 4096
                payload["max_tokens"] = max(max_tokens, floor)
        url = self.provider.base_url.rstrip("/") + "/chat/completions"

        last_status = None
        for http_attempt in range(5):
            response = await client.post(url, headers=_headers(self.provider), json=payload)
            last_status = response.status_code
            if response.status_code in {400, 422}:
                payload.pop("response_format", None)
                response = await client.post(url, headers=_headers(self.provider), json=payload)
                last_status = response.status_code
            if response.status_code in {400, 422} and "reasoning" in payload:
                payload.pop("reasoning", None)
                response = await client.post(url, headers=_headers(self.provider), json=payload)
                last_status = response.status_code
            if response.status_code == 401:
                raise AgentLlmError(
                    f"LLM authentication failed for provider '{self.provider.name}' (401).",
                    hint="Check the provider API key/environment configuration.",
                )
            if response.status_code in {429, 500, 502, 503, 504} and http_attempt < 4:
                retry_after = response.headers.get("retry-after")
                try:
                    wait = float(retry_after) if retry_after else 0.0
                except ValueError:
                    wait = 0.0
                if wait <= 0:
                    wait = min(2.0 * (2 ** http_attempt), 20.0)
                await asyncio.sleep(wait)
                continue
            if response.status_code >= 400:
                body = response.text[:200]
                raise AgentLlmError(
                    f"LLM request failed for provider '{self.provider.name}' "
                    f"with status {response.status_code}: {body}",
                )
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise AgentLlmError("LLM response contained no choices.")
            message = choices[0].get("message") or {}
            content = message.get("content") or message.get("reasoning_content")
            if not isinstance(content, str) or not content.strip():
                raise AgentLlmError("LLM response contained empty content.")
            return content, _usage(data), model

        raise AgentLlmError(
            f"LLM request failed for provider '{self.provider.name}' with status {last_status}."
        )

    async def reason(
        self,
        *,
        system_prompt: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = 1800,
        temperature: float = 0.1,
        trace_recorder: TraceRecorder | None = None,
        stage: str | None = None,
        item_id: str | None = None,
        prompt_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentLlmOutput:
        if self.provider.requires_key and not self.provider.api_key:
            raise AgentLlmError(
                f"No API key configured for provider '{self.provider.name}'.",
                hint="Set the provider API key environment variable.",
            )

        stopwatch = Stopwatch()
        errors: list[str] = []
        attempts = 0
        last_text = ""

        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport
        ) as client:
            resolved_model = await self._resolve_model(client, model)
            for attempt in range(1, self.max_retries + 2):
                attempts = attempt
                attempt_stopwatch = Stopwatch()
                try:
                    text, usage, used_model = await self._post(
                        client,
                        model=resolved_model,
                        system_prompt=system_prompt,
                        prompt=prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    last_text = text
                    payload = _parse_json_object(text)
                    if payload is None:
                        raise AgentLlmError("LLM response was not a valid JSON object.")
                    problems = validate_payload(payload, schema)
                    if problems:
                        raise AgentLlmError(
                            "LLM response failed validation: " + "; ".join(problems)
                        )
                    confidence = payload.get("confidence")
                    record_attempt(
                        trace_recorder,
                        attempt=attempt,
                        stage=stage,
                        item_id=item_id,
                        provider=self.provider.name,
                        model=used_model,
                        prompt_version=prompt_version,
                        system_prompt=system_prompt,
                        prompt=prompt,
                        output=payload,
                        usage=usage,
                        latency_ms=attempt_stopwatch.elapsed_ms,
                        valid=True,
                        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                        metadata=metadata,
                    )
                    return AgentLlmOutput(
                        data=payload,
                        raw_text=text,
                        provider=self.provider.name,
                        model=used_model,
                        prompt_version=prompt_version,
                        attempts=attempt,
                        usage=usage,
                        latency_ms=stopwatch.elapsed_ms,
                        metadata=metadata or {},
                    )
                except Exception as exc:
                    errors.append(str(exc))
                    record_attempt(
                        trace_recorder,
                        attempt=attempt,
                        stage=stage,
                        item_id=item_id,
                        provider=self.provider.name,
                        model=resolved_model,
                        prompt_version=prompt_version,
                        system_prompt=system_prompt,
                        prompt=prompt,
                        output={"error": str(exc), "raw": last_text[:500]},
                        latency_ms=attempt_stopwatch.elapsed_ms,
                        errors=[str(exc)],
                        valid=False,
                        metadata=metadata,
                    )
                    if attempt <= self.max_retries:
                        delay = min(0.5 * (2 ** (attempt - 1)), 4.0)
                        if "429" in str(exc):
                            delay = min(5.0 * attempt, 20.0)
                        await asyncio.sleep(delay)

        hint = "Check NVIDIA_API_KEY/Nemotron endpoint and model, or configure OPENROUTER_API_KEY."
        if self.provider.name == "openrouter":
            hint = "OpenRouter rate limit/model issue; try a Nemotron model or set NVIDIA_API_KEY for NVIDIA NIM."
        raise AgentLlmError(
            f"Agent LLM call failed after {attempts} attempt(s): {errors[-1]}",
            hint=hint,
        )
