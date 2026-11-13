"""Agent client backed by the NVIDIA mini-router.

Routes requests across curated free NVIDIA Nemotron/chat endpoints (e.g.
``nvidia-fast``/``nvidia-auto``) to optimize for fastest successful response
with automatic fallback.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from ..utils import Stopwatch
from .agent_llm import AgentLlmError, AgentLlmOutput, record_attempt, validate_payload
from .llm import _parse_json_object
from .tracing import TraceRecorder

_ROUTER: Any = None
_ROUTER_LOCK = asyncio.Lock()


def _ensure_router_on_path() -> None:
    """Make the ``router`` package importable (env, installed, or checkout)."""

    import importlib.util

    override = os.getenv("PYOPENAPI_ROUTER_PACKAGE") or os.getenv("ROUTER_PACKAGE")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    try:
        spec = importlib.util.find_spec("router")
    except (ImportError, ValueError):
        spec = None
    if spec is not None:
        locations = list(spec.submodule_search_locations or [])
        if spec.origin and not locations:
            locations.append(str(Path(spec.origin).resolve().parent))
        candidates.extend(Path(location) for location in locations)

    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parents[3] / "router" / "src",  # pyopenapi/router/src
            here.parents[4] / "pyopenapi" / "router" / "src",
        ]
    )
    for candidate in candidates:
        try:
            if candidate.is_dir():
                value = str(candidate)
                if value not in sys.path:
                    sys.path.insert(0, value)
        except OSError:
            continue


def router_key_available() -> bool:
    """Return True when the router can resolve its own NVIDIA key."""

    try:
        _ensure_router_on_path()
        from router import load_config  # type: ignore

        return bool(load_config().api_key)
    except Exception:
        return False


async def _get_router() -> Any:
    global _ROUTER
    async with _ROUTER_LOCK:
        if _ROUTER is not None:
            return _ROUTER
        _ensure_router_on_path()
        try:
            from router import MiniRouter, load_config  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise AgentLlmError(
                "NVIDIA router is not available.",
                hint="Ensure pyopenapi/router is present and NVIDIA_API_KEY is configured.",
            ) from exc
        config = load_config()
        if not config.api_key:
            raise AgentLlmError(
                "No NVIDIA_API_KEY configured for router.",
                hint="Set NVIDIA_API_KEY (or NVIDIA_NIM_API_KEY) to use NVIDIA Nemotron routing.",
            )
        router = MiniRouter(config)
        await asyncio.to_thread(router.refresh)
        await router.verify_models()
        _ROUTER = router
        return _ROUTER


class RouterAgentClient:
    provider_name = "router"

    def __init__(
        self,
        *,
        model: str | None = None,
        task_type: str | None = None,
        max_retries: int = 2,
    ) -> None:
        self.model = model or os.getenv("PYOPENAPI_ROUTER_MODEL") or "nvidia-fast"
        self.task_type = task_type
        self.max_retries = max(0, max_retries)

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
        _ensure_router_on_path()
        try:
            from router import RouteRequest  # type: ignore
        except Exception as exc:
            raise AgentLlmError("NVIDIA router import failed.") from exc

        router = await _get_router()
        stopwatch = Stopwatch()
        errors: list[str] = []
        attempts = 0
        last_text = ""
        override = model or self.model or "nvidia-fast"
        task = self.task_type or (stage if stage in {"coding", "agents", "reasoning", "general"} else "agents")

        json_mode = True
        for attempt in range(1, self.max_retries + 2):
            attempts = attempt
            attempt_stopwatch = Stopwatch()
            try:
                req = RouteRequest(
                    prompt=prompt,
                    system=system_prompt,
                    task_type=task,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    model_override=override,
                    cost_mode="free_only",
                )
                result = await router.route(req, allow_fallback=True)
                text = result.content or result.reasoning or ""
                last_text = text
                payload = _parse_json_object(text)
                if payload is None:
                    raise AgentLlmError("LLM response was not a valid JSON object.")
                problems = validate_payload(payload, schema)
                if problems:
                    raise AgentLlmError("LLM response failed validation: " + "; ".join(problems))
                confidence = payload.get("confidence")
                usage_raw = (result.raw or {}).get("usage") or {}
                usage = {
                    k: int(usage_raw[k])
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage_raw.get(k), int)
                }
                record_attempt(
                    trace_recorder,
                    attempt=attempt,
                    stage=stage,
                    item_id=item_id,
                    provider=self.provider_name,
                    model=result.model_id,
                    prompt_version=prompt_version,
                    system_prompt=system_prompt,
                    prompt=prompt,
                    output=payload,
                    usage=usage,
                    latency_ms=attempt_stopwatch.elapsed_ms,
                    valid=True,
                    confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                    metadata={**(metadata or {}), "router_model": override, "attempts": len(result.attempts)},
                )
                return AgentLlmOutput(
                    data=payload,
                    raw_text=text,
                    provider=self.provider_name,
                    model=result.model_id,
                    prompt_version=prompt_version,
                    attempts=attempt,
                    usage=usage,
                    latency_ms=stopwatch.elapsed_ms,
                    metadata={**(metadata or {}), "router_model": override},
                )
            except Exception as exc:
                errors.append(str(exc))
                lowered = str(exc).lower()
                if json_mode and any(token in lowered for token in ("response_format", "http_400", "http_422", "unsupported")):
                    json_mode = False
                record_attempt(
                    trace_recorder,
                    attempt=attempt,
                    stage=stage,
                    item_id=item_id,
                    provider=self.provider_name,
                    model=override,
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
                    await asyncio.sleep(min(0.5 * (2 ** (attempt - 1)), 3.0))

        raise AgentLlmError(
            f"Router agent call failed after {attempts} attempt(s): {errors[-1]}",
            hint="Check NVIDIA_API_KEY and Nemotron router models, or configure OPENROUTER_API_KEY.",
        )
