"""Project metadata reasoning stage."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...prompts import PROMPT_VERSION
from ...prompts.detector import (
    DETECTOR_SCHEMA,
    DETECTOR_SYSTEM_PROMPT,
    build_detector_prompt,
)
from ..agent_llm import AgentLlmClient
from ..tracing import TraceRecorder


class ProjectMetadata(BaseModel):
    title: str
    description: str | None = None
    version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)
    confidence: float | None = None


class DetectorReasoner:
    def __init__(self, client: AgentLlmClient, *, prompt_version: str = PROMPT_VERSION) -> None:
        self.client = client
        self.prompt_version = prompt_version

    async def reason(
        self,
        project: Any,
        *,
        trace_recorder: TraceRecorder | None = None,
        model: str | None = None,
        max_tokens: int = 1200,
    ) -> ProjectMetadata:
        output = await self.client.reason(
            system_prompt=DETECTOR_SYSTEM_PROMPT,
            prompt=build_detector_prompt(project),
            schema=DETECTOR_SCHEMA,
            model=model,
            max_tokens=max_tokens,
            trace_recorder=trace_recorder,
            stage="detector",
            item_id=project.project_id,
            prompt_version=self.prompt_version,
            metadata={"framework": project.framework},
        )
        title = output.data.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else None
        version = output.data.get("version")
        tags = output.data.get("tags")
        confidence = output.data.get("confidence")
        return ProjectMetadata(
            title=title or f"{project.framework} API",
            description=(
                output.data.get("description").strip()
                if isinstance(output.data.get("description"), str)
                else f"OpenAPI specification generated for the {project.framework} project."
            ),
            version=version.strip() if isinstance(version, str) and version.strip() else "0.1.0",
            tags=[
                str(tag).strip()
                for tag in tags or []
                if isinstance(tag, (str, int)) and str(tag).strip()
            ]
            if isinstance(tags, list)
            else [],
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        )
