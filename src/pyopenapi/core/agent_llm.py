"""Agent LLM contracts and provider factory."""

from __future__ import annotations

import os
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..config import Settings
from ..utils import short_hash
from .tracing import ReasoningAttempt, TraceRecorder


class AgentLlmError(RuntimeError):
    """Raised when an agent LLM call cannot produce usable structured output."""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.details = details or {}


class AgentLlmOutput(BaseModel):
    data: dict[str, Any]
    raw_text: str = ""
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    attempts: int = 1
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentLlmClient(Protocol):
    provider_name: str

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
    ) -> AgentLlmOutput:  # pragma: no cover - protocol
        ...


def prompt_hash(system_prompt: str, prompt: str, prompt_version: str | None = None) -> str:
    return short_hash(system_prompt, prompt, prompt_version or "", length=20)


def validate_payload(payload: Any, schema: dict[str, Any] | None) -> list[str]:
    """Perform lightweight structural validation of a JSON payload."""

    if not isinstance(payload, dict):
        return ["response is not a JSON object"]
    if not schema:
        return []
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "object" and not isinstance(payload, dict):
        errors.append("response must be an object")
    for key in schema.get("required") or []:
        if key not in payload:
            errors.append(f"missing required key: {key}")
    return errors


def record_attempt(
    recorder: TraceRecorder | None,
    *,
    attempt: int,
    stage: str | None,
    item_id: str | None,
    provider: str | None,
    model: str | None,
    prompt_version: str | None,
    system_prompt: str,
    prompt: str,
    output: Any,
    usage: dict[str, int] | None = None,
    latency_ms: int = 0,
    errors: list[str] | None = None,
    valid: bool = True,
    fallback_used: bool = False,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReasoningAttempt | None:
    if recorder is None:
        return None
    usage = usage or {}
    return recorder.record_attempt(
        ReasoningAttempt(
            attempt=attempt,
            stage=stage,
            item_id=item_id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash(system_prompt, prompt, prompt_version),
            input_summary={
                "system_prompt": system_prompt[:500],
                "prompt": prompt[:1500],
            },
            output_summary=output,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            latency_ms=latency_ms,
            valid=valid,
            errors=errors or [],
            fallback_used=fallback_used,
            confidence=confidence,
            metadata=metadata or {},
        )
    )


def agent_llm_available(*, settings: Settings | None = None) -> bool:
    """Return True when :func:`create_agent_llm_client` can build a client."""

    from .llm import resolve_provider
    from .router_adapter import router_key_available

    try:
        if router_key_available():
            return True
        return resolve_provider(settings) is not None
    except Exception:
        return False


def create_agent_llm_client(
    *,
    provider_name: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
    max_retries: int = 2,
) -> AgentLlmClient | None:
    """Create an agent client for the active provider, if one is configured.

    Prefers the NVIDIA Nemotron router (fastest free-endpoint routing with
    fallback) when NVIDIA credentials are available.
    """

    requested = (provider_name or "").strip().lower() or "auto"
    if requested in {"legacy", "llm"}:
        from ..engines.legacy_llm_manager_adapter import LegacyLlmManagerAdapter

        return LegacyLlmManagerAdapter(model=model)

    from .llm import nvidia_api_key, resolve_provider
    from .router_adapter import RouterAgentClient, router_key_available

    router_aliases = {"router", "nvidia-router", "nvidia-fast", "nvidia-auto"}
    # Explicit router request
    if requested in router_aliases:
        router_model = model or (requested if requested in {"nvidia-fast", "nvidia-auto"} else "nvidia-fast")
        return RouterAgentClient(model=router_model, max_retries=max_retries)

    # Prefer NVIDIA Nemotron router when the router can resolve its own key
    if requested in {"auto", "nvidia"} and router_key_available():
        router_model = model or os.getenv("PYOPENAPI_ROUTER_MODEL") or "nvidia-fast"
        return RouterAgentClient(model=router_model, max_retries=max_retries)

    provider = resolve_provider(settings, name=None if requested == "auto" else provider_name)
    if provider is not None:
        # Route NVIDIA provider through fastest Nemotron router
        if provider.name == "nvidia" and (nvidia_api_key() or router_key_available()):
            router_model = model or os.getenv("PYOPENAPI_ROUTER_MODEL") or "nvidia-fast"
            return RouterAgentClient(model=router_model, max_retries=max_retries)
        from .llm_adapters import OpenAICompatibleAgentClient

        return OpenAICompatibleAgentClient(provider, model=model, max_retries=max_retries)

    # Last resort: router has a key even though provider resolution missed it
    if router_key_available():
        return RouterAgentClient(model=model or "nvidia-fast", max_retries=max_retries)
    return None
