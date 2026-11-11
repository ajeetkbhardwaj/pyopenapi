"""Shared server dependencies."""

from __future__ import annotations

from functools import lru_cache

from ..config import Settings, get_settings
from ..core.analysis import AnalysisManager


@lru_cache(maxsize=1)
def manager() -> AnalysisManager:
    return AnalysisManager(get_settings())


def settings() -> Settings:
    return get_settings()


def reset_caches() -> None:
    """Used by tests to reload settings/manager."""

    manager.cache_clear()
    get_settings.cache_clear()
