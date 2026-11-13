from __future__ import annotations

from pyopenapi.core.tracing import (
    AgentFinding,
    GenerationTrace,
    ReasoningAttempt,
    TraceRecorder,
    redact,
)


def test_redact_masks_secret_keys_recursively() -> None:
    payload = {
        "api_key": "secret",
        "headers": {"Authorization": "Bearer abc", "Accept": "application/json"},
        "nested": [{"access_token": "xyz", "name": "safe"}],
    }

    cleaned = redact(payload)

    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["headers"]["Authorization"] == "***REDACTED***"
    assert cleaned["headers"]["Accept"] == "application/json"
    assert cleaned["nested"][0]["access_token"] == "***REDACTED***"
    assert cleaned["nested"][0]["name"] == "safe"


def test_trace_recorder_captures_stages_attempts_and_findings() -> None:
    recorder = TraceRecorder(
        analysis_id="abc123",
        project_path="/tmp/demo",
        framework="django",
        provider="ollama",
        model="qwen2.5-coder",
    )

    with recorder.stage("reason_endpoints", item_count=2) as stage:
        stage.success_count = 1
        stage.fallback_count = 1
        recorder.record_attempt(
            ReasoningAttempt(
                attempt=1,
                stage="reason_endpoints",
                item_id="endpoint-1",
                input_summary={"prompt": "describe", "api_key": "secret"},
                output_summary={"summary": "ok", "authorization": "Bearer x"},
                total_tokens=42,
                latency_ms=12,
                valid=False,
                errors=["schema mismatch"],
            )
        )
        recorder.record_finding(
            AgentFinding(
                code="fallback.static",
                message="Fell back to static operation",
                severity="warning",
                item_id="endpoint-1",
                metadata={"token": "secret"},
            )
        )

    trace = recorder.finish()
    assert isinstance(trace, GenerationTrace)
    assert trace.status == "completed"
    assert trace.stages[0].stage == "reason_endpoints"
    assert trace.stages[0].item_count == 2
    assert trace.attempts[0].input_summary["api_key"] == "***REDACTED***"
    assert trace.attempts[0].output_summary["authorization"] == "***REDACTED***"
    assert trace.attempts[0].valid is False
    assert trace.findings[0].metadata["token"] == "***REDACTED***"
    assert trace.trace_id
    assert trace.completed_at is not None
