"""Pydantic models returned by MCP tools and resources."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FrameworkInfo(BaseModel):
    name: str
    display_name: str
    language: str = "python"
    status: Literal["stable", "preview", "planned"]
    analyzer: str
    capabilities: list[str] = Field(default_factory=list)
    notes: str | None = None


class FrameworkList(BaseModel):
    frameworks: list[FrameworkInfo]
    default: str = "auto"


class DetectionResult(BaseModel):
    framework: str
    confidence: float
    supported: bool
    evidence: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    reason: str | None = None


class EndpointParameter(BaseModel):
    name: str
    location: str = "path"
    required: bool | None = None
    type: str | None = None
    description: str | None = None


class EndpointInfo(BaseModel):
    method: str
    path: str
    view: str | None = None
    function: str | None = None
    is_viewset: bool = False
    source_file: str | None = None
    parameters: list[EndpointParameter] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    request_model: str | None = None
    response_model: str | None = None
    response_many: bool = False
    serializer: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FieldInfo(BaseModel):
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


class ComponentInfo(BaseModel):
    name: str
    kind: str = "unknown"
    source_file: str | None = None
    parent_classes: list[str] = Field(default_factory=list)
    fields: list[FieldInfo] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    supports_request: bool | None = None
    supports_response: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisSummary(BaseModel):
    analysis_id: str
    project_path: str
    framework: str
    endpoint_count: int
    component_count: int
    created_at: str
    duration_ms: int
    cache_dir: str
    output_dir: str
    warnings: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    used_dynamic: bool = False
    degraded: bool = False
    cache_bytes: int | None = None


class CacheEntry(BaseModel):
    analysis_id: str
    project_path: str
    framework: str
    created_at: str
    age_seconds: float
    bytes: int


class CacheStats(BaseModel):
    analyses: int
    total_bytes: int
    repos: int = 0
    repo_bytes: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    entries: list[CacheEntry] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    freed_bytes: int = 0
    dry_run: bool = False


class MergeResult(BaseModel):
    output_path: str
    spec_count: int
    path_count: int
    component_count: int
    warnings: list[str] = Field(default_factory=list)


class DiffChange(BaseModel):
    kind: Literal["added", "removed", "changed"]
    path: str
    message: str
    severity: Literal["info", "warning", "error"] = "info"


class DiffResult(BaseModel):
    changed: bool
    breaking: bool
    change_count: int
    changes: list[DiffChange] = Field(default_factory=list)
    summary: str | None = None


class WorkspaceProject(BaseModel):
    name: str
    path: str
    framework: str | None = None
    analysis_id: str | None = None
    spec_path: str | None = None
    ok: bool = True
    error: str | None = None
    endpoint_count: int = 0
    component_count: int = 0


class WorkspaceResult(BaseModel):
    workspace_path: str
    project_count: int
    ok_count: int
    failed_count: int
    projects: list[WorkspaceProject] = Field(default_factory=list)
    merged_spec_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    budget: int = 0
    budget_exceeded: bool = False


class AnalysisList(BaseModel):
    analyses: list[AnalysisSummary]
    count: int


class GenerationResult(BaseModel):
    analysis_id: str | None = None
    project_path: str
    framework: str
    spec_path: str
    format: str = "yaml"
    endpoint_count: int = 0
    component_count: int = 0
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    openapi_version: str | None = None
    title: str | None = None
    llm_usage: LLMUsage | None = None
    dashboard_path: str | None = None


class MarkdownFile(BaseModel):
    path: str
    title: str
    bytes: int


class MarkdownResult(BaseModel):
    output_dir: str
    layout: Literal["multi", "single"] = "multi"
    file_count: int
    files: list[MarkdownFile] = Field(default_factory=list)
    index_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    path: str
    message: str
    severity: Literal["error", "warning", "info"] = "error"
    code: str | None = None


class ValidationReport(BaseModel):
    valid: bool
    issue_count: int
    error_count: int
    warning_count: int
    issues: list[ValidationIssue] = Field(default_factory=list)
    openapi_version: str | None = None
    title: str | None = None
    summary: str | None = None
