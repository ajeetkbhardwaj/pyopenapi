from __future__ import annotations

from pyopenapi.ir.models import (
    Confidence,
    NormalizedComponent,
    NormalizedEndpoint,
    NormalizedField,
    NormalizedOperation,
    NormalizedParameter,
    NormalizedProject,
    SourceRef,
    stable_id,
)


def test_stable_id_is_deterministic() -> None:
    assert stable_id("endpoint", "django", "/users") == stable_id("endpoint", "django", "/users")
    assert stable_id("endpoint", "django", "/users") != stable_id("endpoint", "django", "/items")


def test_ir_models_roundtrip() -> None:
    project = NormalizedProject(
        project_id=stable_id("project", "demo"),
        analysis_id="abc123",
        project_path="/tmp/demo",
        framework="flask",
        endpoints=[
            NormalizedEndpoint(
                endpoint_id=stable_id("endpoint", "flask", "/users"),
                framework="flask",
                path="/users",
                operations=[
                    NormalizedOperation(
                        method="get",
                        operation_id="get_users",
                        parameters=[
                            NormalizedParameter(name="limit", location="query", type="integer")
                        ],
                    )
                ],
                confidence=Confidence(score=0.9, source="static"),
            )
        ],
        components={
            "User": NormalizedComponent(
                component_id=stable_id("component", "User"),
                name="User",
                kind="pydantic_model",
                fields=[NormalizedField(name="id", type="integer", required=True)],
                source_ref=SourceRef(path="app.py", symbol="User"),
            )
        },
    )

    restored = NormalizedProject.model_validate(project.model_dump())
    assert restored == project
    assert restored.endpoints[0].operations[0].parameters[0].name == "limit"
    assert restored.components["User"].fields[0].type == "integer"
