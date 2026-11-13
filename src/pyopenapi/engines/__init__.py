"""Engine adapters that wrap optional legacy pipelines."""

from __future__ import annotations

from .legacy_llm_manager_adapter import LegacyLlmManagerAdapter

__all__ = ["LegacyLlmManagerAdapter"]
