"""Runtime configuration for the pyopenapi MCP server.

All settings can be provided through environment variables prefixed with
``PYOPENAPI_`` or via a ``.env`` file. Defaults are safe for local stdio use.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_OPENAPI_VERSIONS = {"3.0.3", "3.1.0"}


class Settings(BaseSettings):
    """Server and analysis configuration."""

    model_config = SettingsConfigDict(
        env_prefix="PYOPENAPI_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- server ---
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    api_key: str | None = None
    enable_health_endpoints: bool = True
    enable_metrics_endpoint: bool = True
    metrics_path: str = "/metrics"

    # --- HTTP hardening ---
    max_request_bytes: int = 10_000_000
    request_timeout_seconds: float = 1800.0
    rate_limit_per_minute: int = 0  # 0 disables rate limiting
    rate_limit_burst: int = 20
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- paths ---
    cache_dir: Path = Field(default_factory=lambda: Path.home() / ".cache" / "pyopenapi")
    knowl_root: Path | None = None
    output_subdir: str = ".pyopenapi"

    # --- analysis limits ---
    max_scan_files: int = 20_000
    max_file_bytes: int = 2_000_000
    default_use_dynamic: bool = False
    lock_timeout_seconds: float = 900.0

    # --- cache retention ---
    cache_max_age_days: float = 30.0
    cache_max_bytes: int = 5_000_000_000  # 5 GiB; 0 disables the size cap

    # --- agent ---
    agent_preferred_provider: str = "nvidia"

    # --- generation ---
    spec_model: str | None = None
    context_model: str | None = None
    batch_size: int = 30
    max_concurrency: int = 5
    request_spacing: float = 2.0
    generation_timeout_seconds: int = 1800
    openapi_version: str = "3.0.3"

    # --- OpenAI-compatible LLM enrichment (NVIDIA, OpenRouter, local servers) ---
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_spec_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    nvidia_context_model: str = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_spec_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_context_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    llm_timeout_seconds: int = 120
    llm_max_concurrency: int = 4
    llm_endpoints_per_request: int = 40
    llm_max_tokens_budget: int = 0  # 0 disables the token budget

    # --- git / source ---
    github_token: str | None = None
    github_ssh_key: str | None = None

    # --- logging / observability ---
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("cache_dir", "knowl_root", mode="before")
    @classmethod
    def _expand_path(cls, value: object) -> object:
        if value in (None, ""):
            return None
        return Path(str(value)).expanduser().resolve()

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_proxies(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                import json

                try:
                    parsed = json.loads(stripped)
                except ValueError:
                    return []
                return [str(item) for item in parsed] if isinstance(parsed, list) else []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("openapi_version")
    @classmethod
    def _validate_openapi_version(cls, value: str) -> str:
        value = str(value).strip()
        if value not in _OPENAPI_VERSIONS:
            raise ValueError(
                f"openapi_version must be one of {sorted(_OPENAPI_VERSIONS)} (got {value!r})"
            )
        return value

    def resolved_cache_dir(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    # Load .env files before pydantic-settings reads the environment.
    try:
        from .bootstrap import load_env

        load_env()
    except Exception:  # pragma: no cover - dotenv is optional at runtime
        pass
    return Settings()
