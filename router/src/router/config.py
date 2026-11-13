from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {v.strip() for v in value.split(",") if v.strip()}


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


@dataclass(frozen=True)
class RouterConfig:
    api_key: str
    base_url: str = "https://integrate.api.nvidia.com/v1"
    timeout_s: float = 30.0
    connect_timeout_s: float = 10.0
    first_token_timeout_s: float = 12.0
    max_fallback_attempts: int = 3
    telemetry_path: Path = Path(".mini-router/telemetry.jsonl")
    cache_path: Path = Path(".mini-router/cache.json")
    cache_ttl_s: int = 300
    allowed_models: set[str] = field(default_factory=set)
    blocked_models: set[str] = field(default_factory=set)
    free_only: bool = True
    verify_models: bool = False


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_router_env() -> None:
    """Load only the router's own ``.env`` (never the pyopenapi one)."""

    try:
        from dotenv import load_dotenv
    except Exception:
        return
    override = os.getenv("ROUTER_ENV_FILE")
    path = Path(override).expanduser() if override else Path(__file__).resolve().parents[2] / ".env"
    try:
        if path.is_file():
            load_dotenv(path, override=False)
    except Exception:
        pass


def load_config(base_url: str | None = None, api_key: str | None = None) -> RouterConfig:
    _load_router_env()
    key = api_key or os.getenv("NVIDIA_API_KEY") or os.getenv("NVIDIA_NIM_API_KEY") or ""
    url = (base_url or os.getenv("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1").rstrip("/")
    allowed = _parse_csv(os.getenv("NVIDIA_FREE_MODELS") or os.getenv("NVIDIA_ALLOWED_MODELS"))
    blocked = _parse_csv(os.getenv("NVIDIA_BLOCKED_MODELS"))
    return RouterConfig(
        api_key=key,
        base_url=url,
        timeout_s=_get_float("ROUTER_TIMEOUT_S", 30.0),
        connect_timeout_s=_get_float("ROUTER_CONNECT_TIMEOUT_S", 10.0),
        first_token_timeout_s=_get_float("ROUTER_FIRST_TOKEN_TIMEOUT_S", 12.0),
        max_fallback_attempts=max(1, _get_int("ROUTER_MAX_FALLBACK_ATTEMPTS", 3)),
        allowed_models=allowed,
        blocked_models=blocked,
        free_only=True,
        verify_models=_get_bool("ROUTER_VERIFY_MODELS", False),
    )
