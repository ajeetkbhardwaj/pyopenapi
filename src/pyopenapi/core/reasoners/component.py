"""Component reasoning stage.

Agents may only improve descriptions and examples. Field names, types, refs,
required flags and constraints remain exactly what the static analyzers
extracted.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...prompts import PROMPT_VERSION
from ...prompts.component import (
    COMPONENT_SCHEMA,
    COMPONENT_SYSTEM_PROMPT,
    build_component_prompt,
)
from ..agent_llm import AgentLlmClient, AgentLlmError
from ..tracing import TraceRecorder


class FieldPatch(BaseModel):
    description: str | None = None
    example: Any = None


class ComponentReasoning(BaseModel):
    description: str
    fields: dict[str, FieldPatch] = Field(default_factory=dict)
    confidence: float | None = None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


class ComponentReasoner:
    def __init__(self, client: AgentLlmClient, *, prompt_version: str = PROMPT_VERSION) -> None:
        self.client = client
        self.prompt_version = prompt_version

    async def reason(
        self,
        component: Any,
        *,
        trace_recorder: TraceRecorder | None = None,
        model: str | None = None,
        max_tokens: int = 2200,
    ) -> ComponentReasoning:
        try:
            output = await self.client.reason(
                system_prompt=COMPONENT_SYSTEM_PROMPT,
                prompt=build_component_prompt(component),
                schema=COMPONENT_SCHEMA,
                model=model,
                max_tokens=max_tokens,
                trace_recorder=trace_recorder,
                stage="component",
                item_id=component.component_id,
                prompt_version=self.prompt_version,
                metadata={"name": component.name, "kind": component.kind},
            )
            description = _text(output.data.get("description")) or f"{component.name} {component.kind or 'schema'}."
            known_fields = {field.name for field in component.fields}
            raw_fields = output.data.get("fields")
            fields: dict[str, FieldPatch] = {}
            if isinstance(raw_fields, dict):
                for name, raw in raw_fields.items():
                    if str(name) not in known_fields or not isinstance(raw, dict):
                        continue
                    patch = FieldPatch(
                        description=_text(raw.get("description")),
                        example=raw.get("example"),
                    )
                    if patch.description or patch.example is not None:
                        fields[str(name)] = patch
            confidence = output.data.get("confidence")
            return ComponentReasoning(
                description=description,
                fields=fields,
                confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            )
        except Exception as exc:
            raise AgentLlmError(
                f"Component reasoning failed for {getattr(component, 'name', 'component')}: {exc}"
            ) from exc
