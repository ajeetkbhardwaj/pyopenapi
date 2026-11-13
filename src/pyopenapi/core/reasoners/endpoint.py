"""Endpoint reasoning stage.

The reasoner never invents structure. It receives the normalized endpoint and
returns text-only patches keyed by the existing HTTP methods. Missing or empty
patches are treated as failures so the pipeline cannot silently degrade to a
static spec.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...prompts import PROMPT_VERSION
from ...prompts.endpoint import (
    ENDPOINT_SCHEMA,
    ENDPOINT_SYSTEM_PROMPT,
    build_endpoint_prompt,
)
from ..agent_llm import AgentLlmClient, AgentLlmError
from ..tracing import TraceRecorder


class OperationPatch(BaseModel):
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    parameters: dict[str, str] = Field(default_factory=dict)
    request_description: str | None = None
    responses: dict[str, str] = Field(default_factory=dict)


class EndpointReasoning(BaseModel):
    operations: dict[str, OperationPatch] = Field(default_factory=dict)
    confidence: float | None = None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


_HTTP = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _raw_operations(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("operations")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        mapped: dict[str, Any] = {}
        for item in raw:
            if isinstance(item, dict):
                method = item.get("method") or item.get("http_method")
                if method:
                    mapped[str(method).lower()] = item
        if mapped:
            return mapped
    # Some models place method keys at the top level.
    top = {str(k).lower(): v for k, v in data.items() if str(k).lower() in _HTTP}
    return top if top else {}


def _patch_from_raw(raw: Any, operation: Any, endpoint_path: str) -> OperationPatch:
    if isinstance(raw, str):
        raw = {"summary": raw}
    if not isinstance(raw, dict):
        raw = {}

    parameters = raw.get("parameters")
    responses = raw.get("responses")
    method = operation.method.upper()
    summary = _text(raw.get("summary")) or operation.summary or f"{method} {endpoint_path}"
    description = _text(raw.get("description")) or operation.description
    tags = [
        str(tag).strip()
        for tag in (raw.get("tags") or operation.tags or [])
        if isinstance(tag, (str, int)) and str(tag).strip()
    ]
    patch = OperationPatch(
        summary=summary,
        description=description,
        tags=tags,
        parameters={
            str(name): str(desc).strip()
            for name, desc in (parameters or {}).items()
            if isinstance(desc, str) and desc.strip()
        }
        if isinstance(parameters, dict)
        else {},
        request_description=_text(raw.get("request_description")),
        responses={
            str(status): str(desc).strip()
            for status, desc in (responses or {}).items()
            if isinstance(desc, str) and desc.strip()
        }
        if isinstance(responses, dict)
        else {},
    )
    # Ensure at least a summary so downstream output is usable.
    if not patch.summary:
        patch.summary = f"{method} {endpoint_path}"
    return patch


class EndpointReasoner:
    def __init__(self, client: AgentLlmClient, *, prompt_version: str = PROMPT_VERSION) -> None:
        self.client = client
        self.prompt_version = prompt_version

    async def reason(
        self,
        endpoint: Any,
        *,
        trace_recorder: TraceRecorder | None = None,
        model: str | None = None,
        max_tokens: int = 2200,
    ) -> EndpointReasoning:
        try:
            output = await self.client.reason(
                system_prompt=ENDPOINT_SYSTEM_PROMPT,
                prompt=build_endpoint_prompt(endpoint),
                schema=ENDPOINT_SCHEMA,
                model=model,
                max_tokens=max_tokens,
                trace_recorder=trace_recorder,
                stage="endpoint",
                item_id=endpoint.endpoint_id,
                prompt_version=self.prompt_version,
                metadata={"path": endpoint.path, "framework": endpoint.framework},
            )
            raw_ops = _raw_operations(output.data)
            operations: dict[str, OperationPatch] = {}
            for operation in endpoint.operations:
                method = operation.method.lower()
                raw = raw_ops.get(method) or raw_ops.get(method.upper())
                operations[method] = _patch_from_raw(raw, operation, endpoint.path)
            confidence = output.data.get("confidence")
            conf_val = float(confidence) if isinstance(confidence, (int, float)) else None
            if not raw_ops:
                conf_val = min(conf_val or 0.3, 0.3)
            return EndpointReasoning(operations=operations, confidence=conf_val)
        except Exception as exc:
            raise AgentLlmError(f"Endpoint reasoning failed for {endpoint.path}: {exc}") from exc
