from __future__ import annotations

from pathlib import Path
from typing import Any

from .free_registry import FREE_ENDPOINT_MODELS

NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"


def build_litellm_config(api_key_env: str = "NVIDIA_API_KEY") -> dict[str, Any]:
    """Generate LiteLLM-compatible config from curated free-endpoint chat models.

    Groups: nvidia-auto (all chat-capable), nvidia-fast (low-latency/high-throughput chat),
    nvidia-<usage> for key usages, and per-model short names.
    """
    chat_specs = [s for s in FREE_ENDPOINT_MODELS if s.endpoint == "chat" and "safety" not in s.usage]
    fast_specs = [s for s in chat_specs if int(s.speed_tier or 2) == 1]
    model_list: list[dict[str, Any]] = []

    def add(group: str, model_id: str):
        model_list.append({
            "model_name": group,
            "litellm_params": {
                "model": f"nvidia_nim/{model_id}",
                "api_key": f"os.environ/{api_key_env}",
                "api_base": NVIDIA_API_BASE,
                "timeout": 30,
            },
        })

    for s in chat_specs:
        add("nvidia-auto", s.model_id)
    for s in fast_specs:
        add("nvidia-fast", s.model_id)
    for usage_group in ("coding", "agents", "reasoning", "vision", "safety"):
        for s in chat_specs:
            if usage_group in s.usage:
                add(f"nvidia-{usage_group}", s.model_id)
    for s in chat_specs:
        short = s.model_id.split("/")[-1]
        add(short, s.model_id)

    config = {
        "model_list": model_list,
        "litellm_settings": {
            "num_retries": 3,
            "request_timeout": 30,
            "drop_params": True,
            "fallbacks": [
                {"nvidia-fast": ["nvidia-auto"]},
                {"nvidia-coding": ["nvidia-fast", "nvidia-agents", "nvidia-reasoning"]},
                {"nvidia-agents": ["nvidia-fast", "nvidia-coding", "nvidia-reasoning"]},
                {"nvidia-reasoning": ["nvidia-fast", "nvidia-agents", "nvidia-coding"]},
                {"nvidia-auto": ["nvidia-fast"]},
            ],
        },
        "router_settings": {
            "routing_strategy": "latency-based-routing",
            "num_retries": 3,
            "cooldown_time": 60,
            "allowed_fails": 2,
            "enable_pre_call_checks": True,
        },
    }
    return config


def write_litellm_config(path: str | Path, api_key_env: str = "NVIDIA_API_KEY") -> Path:
    try:
        import yaml  # type: ignore
    except Exception as e:
        raise RuntimeError("PyYAML is required for --generate-litellm-config (pip install pyyaml or router[litellm])") from e
    p = Path(path)
    data = build_litellm_config(api_key_env=api_key_env)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p
