"""Tracing and redaction for the agent-native pipeline.

Traces are first-class artifacts: every reasoning attempt, validation failure,
fallback and quality finding can be recorded and emitted as JSON/JSONL without
leaking credentials.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..utils import Stopwatch, utc_now_iso

Severity = Literal["info", "warning", "error"]
TraceStatus = Literal["running", "completed", "failed"]

_REDACT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}
_REDACTED = "***REDACTED***"


def redact(value: Any, *, depth: int = 0, max_depth: int = 12) -> Any:
    """Return a copy of ``value`` with secret-looking keys masked."""

    if depth > max_depth:
        return _REDACTED
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in _REDACT_KEYS):
                result[key_text] = _REDACTED
            else:
                result[key_text] = redact(item, depth=depth + 1, max_depth=max_depth)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, depth=depth + 1, max_depth=max_depth) for item in value]
    return value


class AgentFinding(BaseModel):
    code: str
    message: str
    severity: Severity = "info"
    stage: str | None = None
    item_id: str | None = None
    source_ref: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QualityFinding(AgentFinding):
    category: str = "general"
    score: float | None = None


class StageMetrics(BaseModel):
    stage: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int = 0
    item_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    fallback_count: int = 0
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningAttempt(BaseModel):
    attempt: int = 1
    stage: str | None = None
    item_id: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    input_summary: Any = None
    output_summary: Any = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerationTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    analysis_id: str | None = None
    project_path: str | None = None
    framework: str | None = None
    engine: str = "agent"
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    started_at: str = Field(default_factory=utc_now_iso)
    completed_at: str | None = None
    duration_ms: int = 0
    status: TraceStatus = "running"
    stages: list[StageMetrics] = Field(default_factory=list)
    attempts: list[ReasoningAttempt] = Field(default_factory=list)
    findings: list[AgentFinding] = Field(default_factory=list)
    redacted: bool = True
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceRecorder:
    """Mutable helper that builds a :class:`GenerationTrace`."""

    def __init__(
        self,
        *,
        analysis_id: str | None = None,
        project_path: str | None = None,
        framework: str | None = None,
        engine: str = "agent",
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        redact_values: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.redact_values = redact_values
        self._stopwatch = Stopwatch()
        self.trace = GenerationTrace(
            analysis_id=analysis_id,
            project_path=project_path,
            framework=framework,
            engine=engine,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            metadata=redact(metadata or {}) if redact_values else (metadata or {}),
        )

    def _maybe_redact(self, value: Any) -> Any:
        return redact(value) if self.redact_values else value

    @contextlib.contextmanager
    def stage(
        self,
        name: str,
        *,
        item_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[StageMetrics]:
        metrics = StageMetrics(
            stage=name,
            started_at=utc_now_iso(),
            item_count=item_count,
            metadata=self._maybe_redact(metadata or {}),
        )
        stopwatch = Stopwatch()
        try:
            yield metrics
        except Exception as exc:
            metrics.errors.append(str(exc))
            raise
        finally:
            metrics.completed_at = utc_now_iso()
            metrics.duration_ms = stopwatch.elapsed_ms
            self.trace.stages.append(metrics)

    def record_attempt(self, attempt: ReasoningAttempt) -> ReasoningAttempt:
        if self.redact_values:
            attempt.input_summary = redact(attempt.input_summary)
            attempt.output_summary = redact(attempt.output_summary)
            attempt.metadata = redact(attempt.metadata)
        self.trace.attempts.append(attempt)
        return attempt

    def record_finding(self, finding: AgentFinding) -> AgentFinding:
        if self.redact_values:
            finding.metadata = redact(finding.metadata)
            if finding.source_ref:
                finding.source_ref = redact(finding.source_ref)
        self.trace.findings.append(finding)
        return finding

    def finish(self, *, status: TraceStatus = "completed", error: str | None = None) -> GenerationTrace:
        self.trace.completed_at = utc_now_iso()
        self.trace.duration_ms = self._stopwatch.elapsed_ms
        self.trace.status = status
        self.trace.error = error
        return self.trace
