from __future__ import annotations

import json

from pyopenapi.ir.models import (
    NormalizedComponent,
    NormalizedEndpoint,
    NormalizedField,
    NormalizedOperation,
    NormalizedProject,
    stable_id,
)
from pyopenapi.prompts import (
    COMPONENT_SCHEMA,
    ENDPOINT_SCHEMA,
    PROMPT_VERSION,
    build_component_prompt,
    build_detector_prompt,
    build_endpoint_prompt,
    build_quality_prompt,
)


def _project() -> NormalizedProject:
    endpoint = NormalizedEndpoint(
        endpoint_id=stable_id("endpoint", "flask", "/users"),
        framework="flask",
        path="/users",
        operations=[
            NormalizedOperation(method="get", operation_id="get_users", summary="List users")
        ],
    )
    component = NormalizedComponent(
        component_id=stable_id("component", "User"),
        name="User",
        kind="pydantic_model",
        fields=[NormalizedField(name="id", type="integer", required=True)],
    )
    return NormalizedProject(
        project_id=stable_id("project", "demo"),
        project_path="/tmp/demo",
        framework="flask",
        endpoints=[endpoint],
        components={"User": component},
    )


def test_prompt_version_is_set() -> None:
    assert PROMPT_VERSION


def test_endpoint_prompt_contains_ir_and_schema() -> None:
    project = _project()
    prompt = build_endpoint_prompt(project.endpoints[0])
    payload = json.loads(prompt)
    assert payload["endpoint"]["path"] == "/users"
    assert payload["endpoint"]["operations"][0]["method"] == "get"
    assert ENDPOINT_SCHEMA["required"] == ["operations"]


def test_component_detector_and_quality_prompts_are_json() -> None:
    project = _project()
    component_payload = json.loads(build_component_prompt(project.components["User"]))
    assert component_payload["component"]["name"] == "User"
    assert COMPONENT_SCHEMA["required"] == ["description"]

    detector_payload = json.loads(build_detector_prompt(project))
    assert detector_payload["project"]["framework"] == "flask"
    assert detector_payload["project"]["endpoints"][0]["path"] == "/users"

    quality_payload = json.loads(build_quality_prompt(project))
    assert quality_payload["project"]["components"][0]["name"] == "User"
