from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyopenapi.core.agent_llm import AgentLlmError, AgentLlmOutput
from pyopenapi.core.assembler import assemble_openapi
from pyopenapi.core.pipeline import AgentPipelineError, PipelineOrchestrator
from pyopenapi.core.progress import read_events
from pyopenapi.core.reasoners import (
    ComponentReasoning,
    EndpointReasoning,
    FieldPatch,
    OperationPatch,
    ProjectMetadata,
)
from pyopenapi.errors import LLMNotConfiguredError
from pyopenapi.ir import normalize_analysis


def _record(project_path: Path = Path("/tmp/agent-project")):
    summary = SimpleNamespace(
        analysis_id="agent-analysis",
        project_path=str(project_path),
        framework="django",
        warnings=["source warning"],
        created_at="2026-01-01T00:00:00+00:00",
    )
    return SimpleNamespace(
        summary=summary,
        endpoints=[
            {
                "url": {
                    "url": "/items/{item_id}/",
                    "parameter": [
                        {
                            "name": "item_id",
                            "location": "path",
                            "type": "integer",
                            "required": True,
                        }
                    ],
                },
                "method": "get",
                "view": "ItemViewSet",
                "function": "retrieve",
                "tags": ["items"],
                "response_serializer": "ItemSerializer",
            }
        ],
        components={
            "ItemSerializer": {
                "type": "serializer",
                "path": "api/serializers.py",
                "properties": [
                    {"name": "id", "type": "integer", "read_only": True, "required": True},
                    {"name": "name", "type": "string", "max_length": 100, "required": True},
                ],
            }
        },
        metadata={},
    )


class FakeClient:
    provider_name = "fake"

    def __init__(self, *, fail_endpoint: bool = False) -> None:
        self.fail_endpoint = fail_endpoint
        self.endpoint_calls = 0

    async def reason(
        self,
        *,
        system_prompt: str,
        prompt: str,
        schema=None,
        model=None,
        max_tokens: int = 1800,
        temperature: float = 0.1,
        trace_recorder=None,
        stage=None,
        item_id=None,
        prompt_version=None,
        metadata=None,
    ) -> AgentLlmOutput:
        payload = json.loads(prompt)
        if stage == "detector":
            return AgentLlmOutput(
                data={
                    "title": "Items API",
                    "description": "Item operations.",
                    "version": "1.2.3",
                    "tags": ["items"],
                    "confidence": 0.9,
                },
                provider="fake",
                model="fake-model",
            )
        if stage == "endpoint":
            self.endpoint_calls += 1
            if self.fail_endpoint:
                raise AgentLlmError("endpoint reasoning failed")
            operations = {
                operation["method"]: {
                    "summary": f"{operation['method'].upper()} item",
                    "description": "Agent description.",
                    "tags": ["items"],
                    "parameters": {
                        parameter["name"]: "Item identifier."
                        for parameter in operation.get("parameters", [])
                    },
                    "responses": {
                        response["status"]: "Item response."
                        for response in operation.get("responses", [])
                    },
                }
                for operation in payload["endpoint"]["operations"]
            }
            return AgentLlmOutput(
                data={"operations": operations, "confidence": 0.8},
                provider="fake",
                model="fake-model",
            )
        if stage == "component":
            fields = {
                field["name"]: {"description": f"Field {field['name']}.", "example": "x"}
                for field in payload["component"].get("fields", [])
            }
            return AgentLlmOutput(
                data={
                    "description": f"{payload['component']['name']} component.",
                    "fields": fields,
                    "confidence": 0.8,
                },
                provider="fake",
                model="fake-model",
            )
        raise AssertionError(f"unexpected stage: {stage}")


async def test_pipeline_assembles_agent_output_with_trace(tmp_path: Path) -> None:
    result = await PipelineOrchestrator(client=FakeClient(), max_concurrency=2).run(
        _record(), tmp_path, fmt="json"
    )

    spec = json.loads(Path(result.spec_path).read_text(encoding="utf-8"))
    operation = spec["paths"]["/items/{item_id}/"]["get"]
    component = spec["components"]["schemas"]["ItemSerializer"]

    assert spec["info"]["title"] == "Items API"
    assert spec["info"]["version"] == "1.2.3"
    assert operation["summary"] == "GET item"
    assert operation["parameters"][0]["schema"] == {"type": "integer"}
    assert operation["parameters"][0]["required"] is True
    assert operation["parameters"][0]["description"] == "Item identifier."
    assert component["description"] == "ItemSerializer component."
    assert component["properties"]["id"]["readOnly"] is True
    assert component["properties"]["name"]["description"] == "Field name."
    assert result.trace_path is not None and result.trace_path.exists()
    assert result.progress_path is not None and result.progress_path.exists()
    assert any(event["type"] == "run_done" for event in read_events(result.progress_path))
    assert result.quality_score == 1.0


async def test_pipeline_emits_partial_spec_when_allowed(tmp_path: Path) -> None:
    client = FakeClient(fail_endpoint=True)
    result = await PipelineOrchestrator(client=client, item_retries=2).run(
        _record(), tmp_path, allow_partial=True
    )

    assert client.endpoint_calls == 3
    assert len(result.failed_items) == 1
    assert result.failed_items[0].startswith("endpoint:")
    assert any("partial spec" in warning for warning in result.warnings)
    assert result.spec["paths"] == {}
    assert "ItemSerializer" in result.spec["components"]["schemas"]


async def test_pipeline_fails_without_static_fallback(tmp_path: Path) -> None:
    with pytest.raises(AgentPipelineError):
        await PipelineOrchestrator(client=FakeClient(fail_endpoint=True)).run(
            _record(), tmp_path
        )

    assert not (tmp_path / "openapi.yml").exists()
    trace = json.loads((tmp_path / "agent_trace.json").read_text(encoding="utf-8"))
    assert trace["status"] == "failed"
    events = read_events(tmp_path / "agent_progress.jsonl")
    assert any(event["type"] == "run_error" for event in events)


def test_pipeline_requires_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pyopenapi.core.pipeline.create_agent_llm_client", lambda **_: None)

    with pytest.raises(LLMNotConfiguredError):
        PipelineOrchestrator(client=None)


def test_assembler_preserves_ir_structure() -> None:
    project = normalize_analysis(_record())
    endpoint = project.endpoints[0]
    component = project.components["ItemSerializer"]

    spec = assemble_openapi(
        project,
        endpoint_reasonings={
            endpoint.endpoint_id: EndpointReasoning(
                operations={
                    "get": OperationPatch(
                        summary="Read item",
                        parameters={"item_id": "Item id."},
                        responses={"200": "Item."},
                    )
                }
            )
        },
        component_reasonings={
            component.component_id: ComponentReasoning(
                description="Item schema.",
                fields={"name": FieldPatch(description="Item name.", example="Widget")},
            )
        },
        metadata=ProjectMetadata(title="Items API", version="1.0.0"),
    )

    operation = spec["paths"]["/items/{item_id}/"]["get"]
    schema = spec["components"]["schemas"]["ItemSerializer"]
    assert operation["parameters"][0]["schema"] == {"type": "integer"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ItemSerializer"
    }
    assert schema["required"] == ["id", "name"]
    assert schema["properties"]["name"]["maxLength"] == 100
    assert schema["properties"]["name"]["example"] == "Widget"


async def test_agent_pipeline_flask_and_fastapi(tmp_path: Path, flask_project: Path, fastapi_project: Path) -> None:
    from pyopenapi.core.analysis import AnalysisManager

    manager = AnalysisManager()
    for name, project in [("flask", flask_project), ("fastapi", fastapi_project)]:
        record = manager.analyze(str(project), framework=name, force=True)
        out = tmp_path / name
        result = await PipelineOrchestrator(client=FakeClient(), max_concurrency=2).run(record, out, fmt="json")
        spec = json.loads(Path(result.spec_path).read_text(encoding="utf-8"))
        assert spec["paths"], name
        assert result.trace_path and result.trace_path.exists(), name
        assert result.progress_path and result.progress_path.exists(), name
        assert (out / "agent_trace.json").exists(), name
        assert (out / "agent_progress.jsonl").exists(), name
        events = read_events(result.progress_path)
        assert any(e["type"] == "run_done" for e in events), name
