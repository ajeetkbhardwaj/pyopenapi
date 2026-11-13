"""Legacy ``common.llm.LLMManager`` adapter.

This is the only module that depends on the upstream knowl-apidocs LLM stack.
It is imported lazily so pyopenapi keeps working when the legacy dependencies
(Azure/Gemini/DeepSeek) are not installed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..bootstrap import ensure_knowl_on_path
from ..core.agent_llm import (
    AgentLlmError,
    AgentLlmOutput,
    record_attempt,
    validate_payload,
)
from ..core.llm import _parse_json_object
from ..core.tracing import TraceRecorder
from ..utils import Stopwatch


class LegacyLlmManagerAdapter:
    """Adapt ``common.llm.llm.LLMManager`` to the agent LLM protocol."""

    provider_name = "legacy"

    def __init__(
        self,
        *,
        model: str | None = None,
        max_retries: int = 3,
        error_file: str | None = None,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self.error_file = error_file or str(
            Path(tempfile.gettempdir()) / "pyopenapi-legacy-llm.log"
        )
        self._manager: Any | None = None

    def _load_manager(self) -> Any:
        if self._manager is not None:
            return self._manager
        ensure_knowl_on_path()
        try:
            from common.llm.llm import LLMManager
        except Exception as exc:  # pragma: no cover - environment dependent
            raise AgentLlmError(
                "Legacy LLMManager is unavailable in this environment.",
                hint=(
                    "Install the legacy provider dependencies (Azure/Gemini/DeepSeek) "
                    "or use an OpenAI-compatible provider instead."
                ),
            ) from exc
        self._manager = LLMManager(max_retries=self.max_retries)
        return self._manager

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
        manager = self._load_manager()
        resolved_model = model or self.model
        stopwatch = Stopwatch()
        try:
            content, _ = await manager.generate(
                prompt=prompt,
                error_file=self.error_file,
                system_message=system_prompt,
                model=resolved_model,
                is_json=True,
                max_tokens=max_tokens,
                temperature=temperature,
                entity_id=item_id,
                agent_id=stage or "pyopenapi-agent",
                agent_step=1,
                prefix="[pyopenapi]",
            )
        except Exception as exc:
            raise AgentLlmError(
                f"Legacy LLMManager call failed: {exc}",
                hint="Check the legacy provider configuration or use a different engine.",
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise AgentLlmError(
                "Legacy LLMManager returned no content.",
                hint="Check the legacy provider configuration and logs.",
            )

        payload = _parse_json_object(content)
        if payload is None:
            raise AgentLlmError("Legacy LLMManager response was not a valid JSON object.")
        problems = validate_payload(payload, schema)
        if problems:
            raise AgentLlmError(
                "Legacy LLMManager response failed validation: " + "; ".join(problems)
            )

        confidence = payload.get("confidence")
        record_attempt(
            trace_recorder,
            attempt=1,
            stage=stage,
            item_id=item_id,
            provider="legacy",
            model=resolved_model or "auto",
            prompt_version=prompt_version,
            system_prompt=system_prompt,
            prompt=prompt,
            output=payload,
            latency_ms=stopwatch.elapsed_ms,
            valid=True,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            metadata=metadata,
        )
        return AgentLlmOutput(
            data=payload,
            raw_text=content,
            provider="legacy",
            model=resolved_model or "auto",
            prompt_version=prompt_version,
            attempts=1,
            latency_ms=stopwatch.elapsed_ms,
            metadata=metadata or {},
        )
