"""Agent-native OpenAPI pipeline.

Flow: static analyzers produce evidence -> IR normalization -> per-endpoint and
per-component LLM agent reasoning -> deterministic assembly -> validation.

Generation is LLM-driven end-to-end: an LLM provider is required and reasoning
failures surface as errors (no static-spec fallback).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..errors import LLMNotConfiguredError, SpecGenerationError
from ..ir import NormalizedProject, normalize_analysis
from ..models import LLMUsage
from ..utils import Stopwatch, atomic_write_text, ensure_directory
from .agent_llm import AgentLlmClient, create_agent_llm_client
from .assembler import assemble_openapi
from .progress import PROGRESS_FILENAME, ProgressWriter
from .quality_audit import audit_spec
from .reasoners import (
    ComponentReasoner,
    ComponentReasoning,
    DetectorReasoner,
    EndpointReasoner,
    EndpointReasoning,
    ProjectMetadata,
)
from .tracing import AgentFinding, QualityFinding, TraceRecorder


class AgentPipelineError(SpecGenerationError):
    """Raised when required agent reasoning cannot produce a valid spec."""

    def __init__(self, message: str, *, errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or {}


@dataclass
class AgentPipelineResult:
    spec: dict[str, Any]
    spec_path: Path
    project: NormalizedProject
    metadata: ProjectMetadata
    trace_path: Path | None = None
    progress_path: Path | None = None
    dashboard_path: Path | None = None
    findings: list[QualityFinding] = field(default_factory=list)
    quality_score: float = 1.0
    warnings: list[str] = field(default_factory=list)
    failed_items: list[str] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    duration_ms: int = 0


def _usage_from_trace(recorder: TraceRecorder) -> LLMUsage:
    usage = LLMUsage()
    for attempt in recorder.trace.attempts:
        usage.prompt_tokens += attempt.prompt_tokens
        usage.completion_tokens += attempt.completion_tokens
        usage.total_tokens += attempt.total_tokens
        usage.requests += 1
    return usage


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        client: AgentLlmClient | None = None,
        settings: Settings | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        max_concurrency: int = 4,
        max_retries: int = 2,
        item_retries: int = 1,
        prompt_version: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider_name = provider_name
        self.model = model
        self.client = client or create_agent_llm_client(
            provider_name=provider_name,
            model=model,
            settings=self.settings,
            max_retries=max_retries,
        )
        if self.client is None:
            raise LLMNotConfiguredError(
                "Agent pipeline requires an LLM provider (NVIDIA Nemotron/OpenRouter/Ollama). "
                "Static fallback is not used for agent generation.",
                hint="Set NVIDIA_API_KEY (or NVIDIA_NIM_API_KEY) for Nemotron router, "
                "or OPENROUTER_API_KEY/OLLAMA_HOST, then retry with --engine agent.",
            )
        self.max_concurrency = max(1, max_concurrency)
        self.item_retries = max(0, item_retries)
        self.prompt_version = prompt_version

    async def run(
        self,
        record: Any,
        output_dir: str | Path,
        *,
        fmt: str = "yml",
        openapi_version: str | None = None,
        emit_trace: bool = True,
        emit_progress: bool = True,
        progress_path: str | Path | None = None,
        allow_partial: bool = False,
    ) -> AgentPipelineResult:
        stopwatch = Stopwatch()
        project = normalize_analysis(record)
        target_version = openapi_version or self.settings.openapi_version
        out = Path(output_dir).expanduser().resolve()
        ensure_directory(out)
        resolved_progress_path = (
            Path(progress_path) if progress_path else out / PROGRESS_FILENAME
        )
        progress = ProgressWriter(resolved_progress_path) if emit_progress else None
        if progress is not None:
            progress.reset()
            progress.emit(
                "run_start",
                analysis_id=project.analysis_id,
                project_path=project.project_path,
                framework=project.framework,
                provider=getattr(self.client, "provider_name", None),
                model=self.model,
                openapi_version=target_version,
            )

        recorder = TraceRecorder(
            analysis_id=project.analysis_id,
            project_path=project.project_path,
            framework=project.framework,
            engine="agent",
            provider=getattr(self.client, "provider_name", None),
            model=self.model,
            prompt_version=self.prompt_version,
            metadata={"openapi_version": target_version},
        )

        try:
            detector = DetectorReasoner(self.client, **self._prompt_kwargs())
            if progress is not None:
                progress.emit("stage_start", stage="detector", item_count=1)
            with recorder.stage("detector", item_count=1) as metrics:
                try:
                    metadata = await detector.reason(project, trace_recorder=recorder)
                    metrics.success_count = 1
                except Exception:
                    metrics.failure_count = 1
                    raise
            if progress is not None:
                progress.emit(
                    "stage_end",
                    stage="detector",
                    success=metrics.success_count,
                    failure=metrics.failure_count,
                    duration_ms=metrics.duration_ms,
                )

            endpoint_reasoner = EndpointReasoner(self.client, **self._prompt_kwargs())
            component_reasoner = ComponentReasoner(self.client, **self._prompt_kwargs())
            semaphore = asyncio.Semaphore(self.max_concurrency)

            endpoint_results: dict[str, EndpointReasoning] = {}
            component_results: dict[str, ComponentReasoning] = {}
            errors: dict[str, str] = {}
            partial_warnings: list[str] = []

            async def reason_endpoint(endpoint: Any) -> tuple[str, EndpointReasoning | Exception]:
                label = f"{', '.join(operation.method.upper() for operation in endpoint.operations)} {endpoint.path}"
                if progress is not None:
                    progress.emit(
                        "item_start",
                        kind="endpoint",
                        id=endpoint.endpoint_id,
                        label=label,
                    )
                async with semaphore:
                    last_exc: Exception | None = None
                    for attempt in range(self.item_retries + 1):
                        try:
                            result = await endpoint_reasoner.reason(
                                endpoint,
                                trace_recorder=recorder,
                                model=self.model,
                            )
                        except Exception as exc:
                            last_exc = exc
                            if attempt < self.item_retries:
                                if progress is not None:
                                    progress.emit(
                                        "item_retry",
                                        kind="endpoint",
                                        id=endpoint.endpoint_id,
                                        label=label,
                                        attempt=attempt + 1,
                                        error=str(exc),
                                    )
                                continue
                            break
                        if progress is not None:
                            progress.emit(
                                "item_done",
                                kind="endpoint",
                                id=endpoint.endpoint_id,
                                label=label,
                                confidence=result.confidence,
                            )
                        return endpoint.endpoint_id, result
                    assert last_exc is not None
                    if progress is not None:
                        progress.emit(
                            "item_error",
                            kind="endpoint",
                            id=endpoint.endpoint_id,
                            label=label,
                            error=str(last_exc),
                        )
                    return endpoint.endpoint_id, last_exc

            async def reason_component(component: Any) -> tuple[str, ComponentReasoning | Exception]:
                label = f"{component.name} ({component.kind})"
                if progress is not None:
                    progress.emit(
                        "item_start",
                        kind="component",
                        id=component.component_id,
                        label=label,
                    )
                async with semaphore:
                    last_exc: Exception | None = None
                    for attempt in range(self.item_retries + 1):
                        try:
                            result = await component_reasoner.reason(
                                component,
                                trace_recorder=recorder,
                                model=self.model,
                            )
                        except Exception as exc:
                            last_exc = exc
                            if attempt < self.item_retries:
                                if progress is not None:
                                    progress.emit(
                                        "item_retry",
                                        kind="component",
                                        id=component.component_id,
                                        label=label,
                                        attempt=attempt + 1,
                                        error=str(exc),
                                    )
                                continue
                            break
                        if progress is not None:
                            progress.emit(
                                "item_done",
                                kind="component",
                                id=component.component_id,
                                label=label,
                                confidence=result.confidence,
                            )
                        return component.component_id, result
                    assert last_exc is not None
                    if progress is not None:
                        progress.emit(
                            "item_error",
                            kind="component",
                            id=component.component_id,
                            label=label,
                            error=str(last_exc),
                        )
                    return component.component_id, last_exc

            if progress is not None:
                progress.emit(
                    "stage_start", stage="endpoint", item_count=len(project.endpoints)
                )
            with recorder.stage(
                "endpoint", item_count=len(project.endpoints)
            ) as endpoint_metrics:
                endpoint_tasks = [
                    reason_endpoint(endpoint) for endpoint in project.endpoints
                ]
                for key, value in await asyncio.gather(*endpoint_tasks):
                    if isinstance(value, Exception):
                        errors[f"endpoint:{key}"] = str(value)
                        endpoint_metrics.failure_count += 1
                    else:
                        endpoint_results[key] = value
                        endpoint_metrics.success_count += 1
            if progress is not None:
                progress.emit(
                    "stage_end",
                    stage="endpoint",
                    success=endpoint_metrics.success_count,
                    failure=endpoint_metrics.failure_count,
                    duration_ms=endpoint_metrics.duration_ms,
                )

            if progress is not None:
                progress.emit(
                    "stage_start", stage="component", item_count=len(project.components)
                )
            with recorder.stage(
                "component", item_count=len(project.components)
            ) as component_metrics:
                component_tasks = [
                    reason_component(component)
                    for component in project.components.values()
                ]
                for key, value in await asyncio.gather(*component_tasks):
                    if isinstance(value, Exception):
                        errors[f"component:{key}"] = str(value)
                        component_metrics.failure_count += 1
                    else:
                        component_results[key] = value
                        component_metrics.success_count += 1
            if progress is not None:
                progress.emit(
                    "stage_end",
                    stage="component",
                    success=component_metrics.success_count,
                    failure=component_metrics.failure_count,
                    duration_ms=component_metrics.duration_ms,
                )

            if errors:
                detail = "; ".join(f"{key}: {message}" for key, message in errors.items())
                if not allow_partial:
                    raise AgentPipelineError(
                        f"Agent reasoning failed for {len(errors)} item(s): {detail}",
                        errors=errors,
                    )
                partial_warnings.append(
                    f"Agent reasoning failed for {len(errors)} item(s); partial spec emitted: "
                    f"{detail}"
                )
                recorder.record_finding(
                    AgentFinding(
                        code="partial_results",
                        message=partial_warnings[-1],
                        severity="warning",
                        stage="pipeline",
                    )
                )

            if progress is not None:
                progress.emit("stage_start", stage="assembly", item_count=1)
            with recorder.stage("assembly") as metrics:
                spec = assemble_openapi(
                    project,
                    endpoint_reasonings=endpoint_results,
                    component_reasonings=component_results,
                    metadata=metadata,
                    openapi_version=target_version,
                    strict=not allow_partial,
                )
                metrics.success_count = 1
            if progress is not None:
                progress.emit(
                    "stage_end",
                    stage="assembly",
                    success=metrics.success_count,
                    failure=metrics.failure_count,
                    duration_ms=metrics.duration_ms,
                )

            from .validation import validate_spec

            if progress is not None:
                progress.emit("stage_start", stage="validation", item_count=1)
            with recorder.stage("validation") as metrics:
                report = validate_spec(spec)
                if report.error_count:
                    messages = "; ".join(
                        f"{issue.path}: {issue.message}"
                        for issue in report.issues
                        if issue.severity == "error"
                    )
                    metrics.failure_count = 1
                    raise AgentPipelineError(f"Assembled spec failed validation: {messages}")
                metrics.success_count = 1
            if progress is not None:
                progress.emit(
                    "stage_end",
                    stage="validation",
                    success=metrics.success_count,
                    failure=metrics.failure_count,
                    duration_ms=metrics.duration_ms,
                )

            if progress is not None:
                progress.emit("stage_start", stage="quality", item_count=1)
            with recorder.stage("quality") as metrics:
                findings, score = audit_spec(spec, project)
                for finding in findings:
                    recorder.record_finding(finding)
                metrics.success_count = 1
                metrics.metadata["score"] = score
            if progress is not None:
                progress.emit(
                    "stage_end",
                    stage="quality",
                    success=metrics.success_count,
                    failure=metrics.failure_count,
                    duration_ms=metrics.duration_ms,
                )

            from .spec import write_spec

            spec_path = write_spec(spec, out, fmt=fmt)
            trace = recorder.finish(status="completed")
            trace.metadata["quality_score"] = score
            trace.metadata["failed_items"] = list(errors)
            trace.metadata["endpoint_count"] = len(project.endpoints)
            trace.metadata["component_count"] = len(project.components)
            trace_path = None
            if emit_trace:
                trace_path = out / "agent_trace.json"
                atomic_write_text(
                    trace_path,
                    trace.model_dump_json(indent=2),
                )

            warnings = [
                *project.warnings,
                *partial_warnings,
                *(
                    finding.message
                    for finding in findings
                    if finding.severity == "warning"
                ),
            ]
            usage = _usage_from_trace(recorder)
            if progress is not None:
                progress.emit(
                    "run_done",
                    spec_path=str(spec_path),
                    quality_score=score,
                    duration_ms=stopwatch.elapsed_ms,
                    usage=usage.model_dump(),
                )
            dashboard_path = None
            try:
                from .dashboard import write_dashboard_report

                dashboard_path = write_dashboard_report(
                    out, resolved_progress_path if progress is not None else None
                )
            except Exception:  # dashboard report is best-effort
                dashboard_path = None
            return AgentPipelineResult(
                spec=spec,
                spec_path=spec_path,
                project=project,
                metadata=metadata,
                trace_path=trace_path,
                progress_path=resolved_progress_path if progress is not None else None,
                dashboard_path=dashboard_path,
                findings=findings,
                quality_score=score,
                warnings=warnings,
                failed_items=list(errors),
                usage=usage,
                duration_ms=stopwatch.elapsed_ms,
            )
        except Exception as exc:
            if progress is not None:
                progress.emit("run_error", error=str(exc))
            trace = recorder.finish(status="failed", error=str(exc))
            recorder.record_finding(
                AgentFinding(
                    code="pipeline_failed",
                    message=str(exc),
                    severity="error",
                    stage="pipeline",
                )
            )
            if emit_trace:
                ensure_directory(out)
                atomic_write_text(out / "agent_trace.json", trace.model_dump_json(indent=2))
            try:
                from .dashboard import write_dashboard_report

                write_dashboard_report(
                    out, resolved_progress_path if progress is not None else None
                )
            except Exception:  # dashboard report is best-effort
                pass
            raise

    def _prompt_kwargs(self) -> dict[str, Any]:
        if self.prompt_version:
            return {"prompt_version": self.prompt_version}
        return {}
