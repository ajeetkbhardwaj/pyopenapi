from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

LatencyTarget = Literal["interactive", "background"]
CostMode = Literal["free_only"]


class RouteRequest(BaseModel):
    prompt: str = ""
    system: str | None = None
    messages: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    task_type: str = "general"
    context_tokens: int = 0
    required_capabilities: list[str] = Field(default_factory=list)
    latency_target: LatencyTarget = "interactive"
    cost_mode: CostMode = "free_only"
    temperature: float = 0.0
    max_tokens: int = 4096
    json_mode: bool = False
    disable_thinking: bool | None = None
    model_override: str | None = None


class ModelMeta(BaseModel):
    id: str
    display_name: str = ""
    owned_by: str | None = None
    context_limit: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    usage: list[str] = Field(default_factory=list)
    endpoint: str = "chat"
    speed_tier: int = 2
    accuracy_tier: int = 2
    cost_tier: str = "free"
    status: str = "healthy"
    notes: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class TelemetryRecord(BaseModel):
    model_id: str
    endpoint: str
    task_type: str
    context_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float | None = None
    success: bool = False
    error_type: str | None = None
    retry_count: int = 0
    timestamp: float = Field(default_factory=time.time)


class RouteResult(BaseModel):
    model_id: str
    content: str
    reasoning: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    attempts: list[TelemetryRecord] = Field(default_factory=list)
