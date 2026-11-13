from __future__ import annotations

from dataclasses import dataclass

from .registry import ModelRegistry
from .types import ModelMeta, RouteRequest


@dataclass(frozen=True)
class ScoreWeights:
    capability: float
    accuracy: float
    speed: float
    reliability: float
    traffic: float
    context: float


MODE_WEIGHTS = {
    "interactive": ScoreWeights(capability=0.30, accuracy=0.20, speed=0.30, reliability=0.10, traffic=0.05, context=0.05),
    "background": ScoreWeights(capability=0.30, accuracy=0.25, speed=0.10, reliability=0.15, traffic=0.15, context=0.05),
    "best": ScoreWeights(capability=0.35, accuracy=0.35, speed=0.10, reliability=0.10, traffic=0.05, context=0.05),
}

TASK_USAGE_ALIASES = {
    "code": "coding", "coding": "coding",
    "agent": "agents", "agents": "agents", "agentic": "agents", "tool": "agents",
    "rag": "rag", "retrieval": "rag", "embedding": "embedding", "rerank": "rerank",
    "reason": "reasoning", "reasoning": "reasoning", "math": "reasoning",
    "vision": "vision", "chat": "chat", "general": "chat",
    "document": "document", "ocr": "ocr", "parse": "document",
    "audio": "audio", "speech": "speech", "tts": "tts",
    "translation": "translation", "safety": "safety",
}


def _task_match(model: ModelMeta, req: RouteRequest) -> float:
    tt = TASK_USAGE_ALIASES.get((req.task_type or "").lower(), (req.task_type or "chat").lower())
    usage = set(model.usage)
    if not tt or tt == "general":
        return 0.6
    if tt in usage:
        return 1.0
    if tt == "coding" and ({"agents", "reasoning"} & usage):
        return 0.75
    if tt == "coding" and "code" in model.capabilities:
        return 0.85
    if tt == "agents" and ({"coding", "reasoning"} & usage or "tool_calling" in model.capabilities):
        return 0.85
    if tt == "rag" and ({"embedding", "rerank", "document", "ocr"} & usage):
        return 0.95
    if tt == "chat" and ({"agents", "coding", "reasoning"} & usage):
        return 0.7
    if tt == "reasoning" and "agents" in usage:
        return 0.75
    return 0.25


def _capability_score(model: ModelMeta, req: RouteRequest) -> float:
    score = 0.3 + 0.5 * _task_match(model, req)
    if req.json_mode and "json" in model.capabilities:
        score += 0.1
    if "tool_calling" in req.required_capabilities and "tool_calling" in model.capabilities:
        score += 0.1
    if "vision" in req.required_capabilities and "vision" in model.capabilities:
        score += 0.1
    return max(0.0, min(score, 1.0))


def _tier_score(tier: int, best: float = 1.0, mid: float = 0.7, low: float = 0.45) -> float:
    return {1: best, 2: mid, 3: low}.get(int(tier or 2), mid)


def _health_scores(reg: ModelRegistry, model: ModelMeta) -> tuple[float, float, float]:
    t = reg.telemetry.get(model.id, {})
    lat = t.get("avg_latency_ms")
    err = t.get("error_rate", 0.0)
    succ = t.get("success_count", 0)
    speed_prior = _tier_score(model.speed_tier)
    if lat is None:
        speed = speed_prior
        traffic = 0.7
    else:
        observed_speed = max(0.0, min(1.0, 1.0 - (float(lat) / 15000.0)))
        speed = 0.7 * observed_speed + 0.3 * speed_prior
        traffic = max(0.0, min(1.0, 1.0 - (float(lat) / 20000.0)))
    reliability = max(0.0, min(1.0, 1.0 - float(err))) if (succ or err) else 0.75
    return speed, reliability, traffic


def _context_score(model: ModelMeta, req: RouteRequest) -> float:
    if not req.context_tokens or not model.context_limit:
        return 0.7
    headroom = model.context_limit - req.context_tokens
    if headroom < 0:
        return 0.0
    return min(1.0, 0.5 + headroom / max(model.context_limit, 1))


def score_model(reg: ModelRegistry, model: ModelMeta, req: RouteRequest) -> float:
    task = (req.task_type or "").lower()
    mode = "best" if task in {"reasoning", "math"} else ("interactive" if req.latency_target == "interactive" else "background")
    w = MODE_WEIGHTS[mode]
    cap = _capability_score(model, req)
    acc = _tier_score(model.accuracy_tier)
    speed, reliability, traffic = _health_scores(reg, model)
    ctx = _context_score(model, req)
    score = (
        w.capability * cap
        + w.accuracy * acc
        + w.speed * speed
        + w.reliability * reliability
        + w.traffic * traffic
        + w.context * ctx
    )
    # prefer chat-completion endpoints for chat-style tasks
    if task in {"coding", "agents", "reasoning", "chat", "general", "vision", "translation"} and model.endpoint != "chat":
        score *= 0.2
    return score


def rank_models(reg: ModelRegistry, req: RouteRequest) -> list[tuple[ModelMeta, float]]:
    eligible = reg.eligible(req)
    ranked = sorted(((m, score_model(reg, m, req)) for m in eligible), key=lambda x: x[1], reverse=True)
    return ranked
