"""Versioned prompt contracts for agent-native generation."""

from __future__ import annotations

from .component import COMPONENT_SCHEMA, COMPONENT_SYSTEM_PROMPT, build_component_prompt
from .detector import DETECTOR_SCHEMA, DETECTOR_SYSTEM_PROMPT, build_detector_prompt
from .endpoint import ENDPOINT_SCHEMA, ENDPOINT_SYSTEM_PROMPT, build_endpoint_prompt
from .quality import QUALITY_SCHEMA, QUALITY_SYSTEM_PROMPT, build_quality_prompt

PROMPT_VERSION = "2026-11-11.1"

__all__ = [
    "COMPONENT_SCHEMA",
    "COMPONENT_SYSTEM_PROMPT",
    "DETECTOR_SCHEMA",
    "DETECTOR_SYSTEM_PROMPT",
    "ENDPOINT_SCHEMA",
    "ENDPOINT_SYSTEM_PROMPT",
    "PROMPT_VERSION",
    "QUALITY_SCHEMA",
    "QUALITY_SYSTEM_PROMPT",
    "build_component_prompt",
    "build_detector_prompt",
    "build_endpoint_prompt",
    "build_quality_prompt",
]
