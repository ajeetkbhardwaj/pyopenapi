from __future__ import annotations

import re
from dataclasses import dataclass, field

from .free_registry import FREE_ENDPOINT_MODELS, FreeModelSpec
from .types import ModelMeta, RouteRequest

MODEL_GROUPS = {"nvidia-auto", "nvidia-fast", "router-auto", "auto"}


def _infer_context_limit(model_id: str) -> int | None:
    mid = model_id.lower()
    for pat, val in [(r"1m", 1_000_000), (r"200k", 200_000), (r"128k", 128_000), (r"32k", 32_768), (r"16k", 16_384), (r"8k", 8192)]:
        if re.search(pat, mid):
            return val
    return None


def _capabilities_for(spec: FreeModelSpec) -> list[str]:
    caps: set[str] = set()
    u = set(spec.usage)
    if spec.endpoint == "chat" or (u & {"chat", "agents", "coding", "reasoning", "vision", "translation", "safety"}):
        caps.add("chat")
    if "coding" in u:
        caps.add("code")
    if {"agents", "reasoning", "coding"} & u:
        caps.update({"tool_calling", "json", "instruction"})
    if "vision" in u:
        caps.add("vision")
    if "safety" in u:
        caps.add("safety")
    if spec.endpoint == "embeddings":
        caps.update({"embedding"})
    if spec.endpoint == "rerank":
        caps.update({"rerank"})
    if spec.endpoint == "parse":
        caps.update({"document", "ocr"})
    if spec.endpoint == "audio":
        caps.update({"audio"})
    return sorted(caps)


def is_chat_model(m: ModelMeta) -> bool:
    return m.endpoint == "chat" and not set(m.usage).issubset({"safety"})


def is_fast_chat_model(m: ModelMeta) -> bool:
    # fast/low-latency, throughput-oriented chat models
    return is_chat_model(m) and int(m.speed_tier or 2) == 1


@dataclass
class ModelRegistry:
    models: dict[str, ModelMeta] = field(default_factory=dict)
    telemetry: dict[str, dict] = field(default_factory=dict)
    allowed_models: set[str] = field(default_factory=set)
    blocked_models: set[str] = field(default_factory=set)

    def load(self) -> None:
        self.models.clear()
        for spec in FREE_ENDPOINT_MODELS:
            mid = spec.model_id
            if self.blocked_models and mid in self.blocked_models:
                continue
            if self.allowed_models and mid not in self.allowed_models:
                continue
            meta = ModelMeta(
                id=mid,
                display_name=spec.display_name,
                owned_by=spec.provider,
                context_limit=_infer_context_limit(mid),
                capabilities=_capabilities_for(spec),
                usage=list(spec.usage),
                endpoint=spec.endpoint,
                speed_tier=spec.speed_tier,
                accuracy_tier=spec.accuracy_tier,
                cost_tier="free",
                status="healthy",
                notes=spec.notes,
                raw={},
            )
            self.models[mid] = meta

    def get(self, model_id: str) -> ModelMeta | None:
        return self.models.get(model_id)

    def retain(self, available: set[str]) -> list[str]:
        """Drop registry entries not present upstream; return removed ids."""

        if not available:
            return []
        removed = sorted(model_id for model_id in self.models if model_id not in available)
        for model_id in removed:
            self.models.pop(model_id, None)
        return removed

    def resolve_group(self, name: str | None) -> str | None:
        if not name:
            return None
        n = name.strip().lower()
        if n in {"nvidia-auto", "router-auto", "auto"}:
            return "nvidia-auto"
        if n == "nvidia-fast":
            return "nvidia-fast"
        return None

    def pool(self, group: str | None = None) -> list[ModelMeta]:
        models = list(self.models.values())
        g = self.resolve_group(group)
        if g == "nvidia-auto":
            models = [m for m in models if is_chat_model(m)]
        elif g == "nvidia-fast":
            models = [m for m in models if is_fast_chat_model(m)]
        return models

    def eligible(self, req: RouteRequest) -> list[ModelMeta]:
        tt = (req.task_type or "general").lower()
        group = self.resolve_group(req.model_override)
        candidates = self.pool(group) if group else list(self.models.values())
        out: list[ModelMeta] = []
        for m in candidates:
            if m.cost_tier != "free" or m.status == "unavailable":
                continue
            if req.context_tokens and m.context_limit and req.context_tokens > m.context_limit:
                continue
            u = set(m.usage)
            ep = m.endpoint
            # endpoint/task compatibility
            if tt in {"rag", "embedding", "retrieval"}:
                if ep not in {"embeddings", "rerank", "parse"} and not ({"rag", "embedding", "rerank", "document", "ocr"} & u):
                    continue
            elif tt == "rerank":
                if ep != "rerank" and "rerank" not in u:
                    continue
            elif tt == "safety":
                if ep != "chat" or "safety" not in u:
                    continue
            elif tt in {"document", "ocr", "parse"}:
                if ep != "parse" and not ({"document", "ocr"} & u):
                    continue
            elif tt in {"audio", "speech", "tts"}:
                if ep != "audio" and not ({"audio", "speech", "tts"} & u):
                    continue
            else:
                # generative/chat-completion tasks require chat endpoint
                if ep != "chat":
                    continue
                if u.issubset({"safety"}):
                    continue
            # strict capability requirements
            if req.required_capabilities:
                missing = [c for c in req.required_capabilities if c not in m.capabilities]
                strict = {"vision", "long_context", "tool_calling"}
                if any(c in strict for c in missing):
                    continue
            out.append(m)
        return out
