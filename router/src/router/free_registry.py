"""Curated registry of NVIDIA Build free-endpoint models (user-provided list).

model_id values are best-effort NVIDIA Integrate API identifiers; verify against
https://build.nvidia.com/models?filters=nimType%3Anim_type_preview and set
NVIDIA_FREE_MODELS to override/confirm in your environment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FreeModelSpec:
    display_name: str
    model_id: str
    provider: str
    usage: tuple[str, ...]
    endpoint: str = "chat"  # chat | embeddings | rerank | parse | audio | vision | other
    speed_tier: int = 2  # 1=fastest, 3=slowest (rough prior)
    accuracy_tier: int = 2  # 1=highest, 3=lowest (rough prior for its category)
    notes: str = ""


FREE_ENDPOINT_MODELS: list[FreeModelSpec] = [
    FreeModelSpec("kimi-k3.1-pro", "moonshotai/kimi-k3.1-pro", "moonshotai", ("agents", "coding", "reasoning", "chat"), "chat", 3, 1, "3T/1M ctx, agentic coding/3D/finance"),
    FreeModelSpec("Mistral Large 4", "mistralai/mistral-large", "mistralai", ("agents", "coding", "reasoning", "vision", "chat"), "chat", 3, 1, "1T/52B MoE, multimodal, tool calling, structured outputs"),
    FreeModelSpec("nemotron-3.5-parse", "nvidia/nemotron-3.5-parse", "nvidia", ("document", "ocr", "rag"), "parse", 1, 1, "text/table/layout extraction from images"),
    FreeModelSpec("Nemotron 3.5 Bolt E4B", "nvidia/nemotron-3.5-bolt-e4b", "nvidia", ("agents", "reasoning", "chat"), "chat", 1, 2, "compact reasoning/tool-calling/agentic chat"),
    FreeModelSpec("nemotron-3.5-rerank-8b", "nvidia/nemotron-3.5-rerank-8b", "nvidia", ("rag", "rerank"), "rerank", 1, 1, "multimodal reranking for retrieval"),
    FreeModelSpec("nemotron-3.5-embed-1b", "nvidia/nemotron-3.5-embed-1b", "nvidia", ("rag", "embedding"), "embeddings", 1, 2, "1B multimodal embedding for RAG"),
    FreeModelSpec("nemotron-3.5-embed-8b", "nvidia/nemotron-3.5-embed-8b", "nvidia", ("rag", "embedding"), "embeddings", 2, 1, "8B multimodal embedding for RAG"),
    FreeModelSpec("minimax-m3.1-flash", "minimaxai/minimax-m3.1-flash", "minimaxai", ("agents", "coding", "chat"), "chat", 1, 2, "native multimodal MoE, coding/agentic/computer use"),
    FreeModelSpec("rfd4-proteina", "ipd/rfd4-proteina", "ipd", ("biology", "science"), "other", 2, 2, "protein/enzyme/binder/antibody design"),
    FreeModelSpec("nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia", ("agents", "coding", "reasoning", "vision", "chat"), "chat", 3, 1, "multimodal reasoning, agentic/coding/tool-calling"),
    FreeModelSpec("Step 5 Preview", "stepfun-ai/step-5-preview", "stepfun-ai", ("agents", "reasoning", "vision", "chat"), "chat", 3, 1, "~600B/27B multimodal MoE, 1M ctx"),
    FreeModelSpec("Kumo Tabular", "kumo-ai/kumo-tabular", "kumo-ai", ("tabular", "prediction"), "other", 1, 2, "tabular in-context learning"),
    FreeModelSpec("3D Body Pose", "nvidia/3d-body-pose", "nvidia", ("vision", "3d", "pose"), "vision", 1, 2, "3D human body pose/skeleton"),
    FreeModelSpec("deepseek-v4.1-flash", "deepseek-ai/deepseek-v4.1-flash", "deepseek-ai", ("agents", "coding", "reasoning", "vision", "chat"), "chat", 2, 1, "552B MoE/8B active, native multimodal"),
    FreeModelSpec("Kumo Relational", "kumo-ai/kumo-relational", "kumo-ai", ("tabular", "prediction", "relational"), "other", 2, 2, "relational foundation model"),
    FreeModelSpec("glm-5-3-flash", "z-ai/glm-5.3-flash", "z-ai", ("agents", "reasoning", "vision", "chat"), "chat", 2, 1, "320B/18B MoE multimodal, reasoning/tool calling"),
    FreeModelSpec("nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia", ("agents", "coding", "chat"), "chat", 1, 2, "fast 30B A3B MoE for agentic tasks"),
    FreeModelSpec("muse-glimmer-30b", "meta/muse-glimmer-30b", "meta", ("agents", "reasoning", "vision", "chat"), "chat", 2, 2, "multimodal reasoning with tool-calling"),
    FreeModelSpec("riva-translate-4b-instruct-v2", "nvidia/riva-translate-4b-instruct-v2", "nvidia", ("translation", "chat"), "chat", 1, 2, "translation in 37 languages"),
    FreeModelSpec("ising-calibration-1.5-31b", "nvidia/ising-calibration-1.5-31b", "nvidia", ("vision", "science", "chat"), "chat", 2, 2, "quantum calibration chart VLM"),
    FreeModelSpec("nemotron-3-embed-1b", "nvidia/nemotron-3-embed-1b", "nvidia", ("rag", "embedding"), "embeddings", 1, 2, "1B embedding for semantic search/RAG"),
    FreeModelSpec("laguna-xs-2.1", "poolside/laguna-xs-2.1", "poolside", ("agents", "coding", "chat"), "chat", 1, 2, "33B MoE for local long-horizon agentic coding"),
    FreeModelSpec("diffusiongemma-26b-a4b-it", "google/diffusiongemma-26b-a4b-it", "google", ("chat",), "chat", 2, 2, "diffusion LLM, parallel token generation"),
    FreeModelSpec("nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3-ultra-550b-a55b", "nvidia", ("agents", "coding", "reasoning", "chat"), "chat", 3, 1, "large MoE 1M ctx, agentic reasoning/coding/planning"),
    FreeModelSpec("nemotron-3.5-content-safety", "nvidia/nemotron-3.5-content-safety", "nvidia", ("safety",), "chat", 1, 1, "multilingual multimodal unsafe/toxic content detection"),
    FreeModelSpec("cosmos3-nano", "nvidia/cosmos3-nano", "nvidia", ("vision", "video", "world-model"), "other", 2, 2, "image/video/action generation with spatial controls"),
    FreeModelSpec("cosmos3-nano-reasoner", "nvidia/cosmos3-nano-reasoner", "nvidia", ("vision", "reasoning", "world-model"), "chat", 2, 2, "physical world reasoning on video/images"),
    FreeModelSpec("nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia", ("agents", "reasoning", "vision", "chat"), "chat", 2, 2, "omni-modal reasoning (image/video/speech/text)"),
    FreeModelSpec("synthetic-video-detector", "nvidia/synthetic-video-detector", "nvidia", ("safety", "video", "detection"), "other", 1, 2, "AI-generated/synthetic video detection"),
    FreeModelSpec("Active Speaker Detection", "nvidia/active-speaker-detection", "nvidia", ("audio", "video", "vision"), "other", 1, 2, "detect/track speaker identities in video"),
    FreeModelSpec("ising-calibration-1-35b-a3b", "nvidia/ising-calibration-1-35b-a3b", "nvidia", ("vision", "science"), "chat", 2, 2, "quantum calibration chart understanding VLM"),
    FreeModelSpec("gemma-4-31b-it", "google/gemma-4-31b-it", "google", ("agents", "coding", "reasoning", "chat"), "chat", 2, 2, "dense 31B, reasoning/coding/agentic"),
    FreeModelSpec("cosmos-transfer2.5-2b", "nvidia/cosmos-transfer2.5-2b", "nvidia", ("video", "world-model", "simulation"), "other", 2, 2, "physics-aware video world states (deprecation noted)"),
    FreeModelSpec("streampetr", "nvidia/streampetr", "nvidia", ("vision", "autonomous-driving"), "other", 1, 2, "efficient 3D object detection for AV"),
    FreeModelSpec("llama-3.1-nemotron-safety-guard-8b-v3", "nvidia/llama-3.1-nemotron-safety-guard-8b-v3", "nvidia", ("safety",), "chat", 1, 1, "multilingual content safety/moderation"),
    FreeModelSpec("gpt-oss-20b", "openai/gpt-oss-20b", "openai", ("reasoning", "chat"), "chat", 1, 2, "20B MoE for reasoning/math"),
    FreeModelSpec("llama-guard-4-12b", "meta/llama-guard-4-12b", "meta", ("safety",), "chat", 1, 1, "multimodal safety classification"),
    FreeModelSpec("Background Noise Removal", "nvidia/background-noise-removal", "nvidia", ("audio", "speech"), "audio", 1, 2, "remove noise, improve speech intelligibility"),
    FreeModelSpec("magpie-tts-zeroshot", "nvidia/magpie-tts-zeroshot", "nvidia", ("audio", "tts"), "audio", 1, 2, "zero-shot expressive TTS"),
    FreeModelSpec("sparsedrive", "nvidia/sparsedrive", "nvidia", ("vision", "autonomous-driving"), "other", 2, 2, "end-to-end AV perception/prediction/planning"),
    FreeModelSpec("bevformer", "nvidia/bevformer", "nvidia", ("vision", "autonomous-driving", "3d"), "other", 2, 2, "BEV 3D perception for AV"),
    FreeModelSpec("Studio Voice", "nvidia/studio-voice", "nvidia", ("audio", "speech"), "audio", 1, 2, "enhance speech to studio quality"),
    FreeModelSpec("llama-3.2-90b-vision-instruct", "meta/llama-3.2-90b-vision-instruct", "meta", ("vision", "reasoning", "chat"), "chat", 3, 1, "vision-language model, high-quality reasoning from images"),
    FreeModelSpec("paligemma", "google/paligemma", "google", ("vision", "chat"), "chat", 2, 2, "vision-language, text+visual understanding"),
]


def get_free_models() -> list[FreeModelSpec]:
    return list(FREE_ENDPOINT_MODELS)


def get_by_id(model_id: str) -> FreeModelSpec | None:
    for m in FREE_ENDPOINT_MODELS:
        if m.model_id == model_id:
            return m
    return None
