"""Normalized IR models shared by every framework and agent stage.

The IR is the contract between framework-specific analyzers and the
agent-native pipeline. Normalizers translate raw analyzer dictionaries into
these models; reasoners, assemblers and emitters only consume the IR.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..utils import short_hash

ConfidenceSource = Literal["static", "llm", "merged", "fallback"]


def stable_id(*parts: object, length: int = 16) -> str:
    """Return a deterministic, stable identifier for IR entities."""

    return short_hash(*parts, length=length)


class SourceRef(BaseModel):
    """Pointer back to the source that produced a normalized entity."""

    path: str | None = None
    symbol: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Confidence(BaseModel):
    score: float = 1.0
    rationale: str | None = None
    source: ConfidenceSource = "static"


class AuthHint(BaseModel):
    schemes: list[str] = Field(default_factory=list)
    required: bool | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextHint(BaseModel):
    name: str
    value: Any = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedParameter(BaseModel):
    name: str
    location: str = "path"
    required: bool | None = None
    type: str | None = None
    format: str | None = None
    description: str | None = None
    schema_data: dict[str, Any] = Field(default_factory=dict)
    source: SourceRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedRequest(BaseModel):
    model_ref: str | None = None
    required: bool = True
    content_type: str = "application/json"
    schema_data: dict[str, Any] = Field(default_factory=dict)
    source: SourceRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedResponse(BaseModel):
    status: str = "200"
    description: str = "Successful response"
    model_ref: str | None = None
    many: bool = False
    content_type: str = "application/json"
    schema_data: dict[str, Any] = Field(default_factory=dict)
    source: SourceRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedOperation(BaseModel):
    method: str
    operation_id: str
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    parameters: list[NormalizedParameter] = Field(default_factory=list)
    request: NormalizedRequest | None = None
    responses: list[NormalizedResponse] = Field(default_factory=list)
    security: list[dict[str, Any]] = Field(default_factory=list)
    security_schemes: dict[str, Any] = Field(default_factory=dict)
    deprecated: bool = False
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedEndpoint(BaseModel):
    endpoint_id: str
    framework: str
    path: str
    operations: list[NormalizedOperation] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedField(BaseModel):
    name: str
    type: str | None = None
    format: str | None = None
    ref: str | None = None
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None
    additional_properties: dict[str, Any] | None = None
    max_length: int | None = None
    required: bool | None = None
    nullable: bool | None = None
    read_only: bool | None = None
    write_only: bool | None = None
    description: str | None = None
    default: Any = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedComponent(BaseModel):
    component_id: str
    name: str
    kind: str = "unknown"
    source_ref: SourceRef | None = None
    parent_classes: list[str] = Field(default_factory=list)
    fields: list[NormalizedField] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    supports_request: bool | None = None
    supports_response: bool | None = None
    confidence: Confidence = Field(default_factory=Confidence)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedProject(BaseModel):
    project_id: str
    analysis_id: str | None = None
    project_path: str
    framework: str
    title: str | None = None
    version: str = "0.1.0"
    endpoints: list[NormalizedEndpoint] = Field(default_factory=list)
    components: dict[str, NormalizedComponent] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    confidence: Confidence = Field(default_factory=Confidence)
    metadata: dict[str, Any] = Field(default_factory=dict)
