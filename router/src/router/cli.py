from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import load_config
from .free_registry import FREE_ENDPOINT_MODELS
from .router import MiniRouter
from .types import RouteRequest


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="router: NVIDIA free-endpoint router (curated registry, nvidia-fast/nvidia-auto, chat service)")
    p.add_argument("prompt", nargs="*", help="Prompt to send (chat-completion tasks)")
    p.add_argument("--base-url", help="NVIDIA API base URL")
    p.add_argument("--model", default="nvidia-auto", help="Model id or group: nvidia-auto (all chat), nvidia-fast (low-latency chat), or specific model id")
    p.add_argument("--system", help="Optional system prompt")
    p.add_argument("--task-type", default="general", help="Task type (general|coding|agents|reasoning|vision|chat|rag|embedding|rerank|document|audio|safety|translation)")
    p.add_argument("--latency-target", choices=["interactive", "background"], default="interactive")
    p.add_argument("--json", action="store_true", help="Request JSON response format")
    p.add_argument("--stream", action="store_true", help="Stream chat completion tokens (including reasoning/thinking when provided)")
    p.add_argument("--no-think", action="store_true", help="Disable model thinking/reasoning for lower latency (default for nvidia-fast/interactive unless task is reasoning)")
    p.add_argument("--think", action="store_true", help="Enable model thinking/reasoning when supported")
    p.add_argument("--list-models", action="store_true", help="List curated free-endpoint models/groups")
    p.add_argument("--category", help="Filter --list-models by category/group (e.g. nvidia-auto, nvidia-fast, coding, agents, rag)")
    p.add_argument("--rank", action="store_true", help="Dry-run: show ranked eligible models for the request/task/pool (no API call)")
    p.add_argument("--no-fallback", action="store_true", help="Disable fallback attempts")
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--generate-litellm-config", metavar="PATH", help="Generate LiteLLM proxy config YAML from curated chat models and exit")
    p.add_argument("--serve", action="store_true", help="Run OpenAI-compatible HTTP router service (streaming + thinking supported)")
    p.add_argument("--host", default="127.0.0.1", help="Host for --serve (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Port for --serve (default 8000)")
    return p


async def _async_main() -> int:
    args = _build_parser().parse_args()

    if args.generate_litellm_config:
        try:
            from .litellm_config import write_litellm_config
            out = write_litellm_config(args.generate_litellm_config)
            chat_count = sum(1 for s in FREE_ENDPOINT_MODELS if s.endpoint == "chat" and "safety" not in s.usage)
            fast_count = sum(1 for s in FREE_ENDPOINT_MODELS if s.endpoint == "chat" and "safety" not in s.usage and int(s.speed_tier or 2) == 1)
            print(f"Wrote LiteLLM config: {out} (nvidia-auto chat={chat_count}, nvidia-fast={fast_count})")
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1

    cfg = load_config(base_url=args.base_url)

    if args.serve:
        if not cfg.api_key:
            print("Error: NVIDIA_API_KEY is required to run --serve (set env var or .env)")
            return 1
        try:
            from .server import serve
            serve(cfg, host=args.host, port=args.port)
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1

    router = MiniRouter(cfg)
    router.refresh()
    await router.verify_models()

    if args.list_models:
        models = router.list_models(args.category)
        if not models:
            print("No matching free-endpoint models found.")
            return 1
        for m in models:
            caps = ",".join(m.capabilities) if m.capabilities else "-"
            usage = ",".join(m.usage) if m.usage else "-"
            print(
                f"{m.id}\tname={m.display_name}\tendpoint={m.endpoint}\tusage={usage}\t"
                f"caps={caps}\tspeed_tier={m.speed_tier}\taccuracy_tier={m.accuracy_tier}\tfree_endpoint=yes"
            )
        return 0

    prompt = " ".join(args.prompt).strip()
    disable_thinking = None
    if args.no_think:
        disable_thinking = True
    elif args.think:
        disable_thinking = False
    req = RouteRequest(
        prompt=prompt or "(rank-only)",
        system=args.system,
        task_type=args.task_type,
        latency_target=args.latency_target,
        json_mode=args.json,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        disable_thinking=disable_thinking,
        model_override=args.model,
        cost_mode="free_only",
    )

    if args.rank:
        ranked = router.rank(req)
        if not ranked:
            print("No eligible models for this request/task/pool.")
            return 1
        for i, (m, score) in enumerate(ranked, 1):
            print(f"{i:02d}\tscore={score:.4f}\t{m.id}\tendpoint={m.endpoint}\tusage={','.join(m.usage)}\tspeed={m.speed_tier}\taccuracy={m.accuracy_tier}")
        return 0

    if not prompt:
        _build_parser().print_help()
        return 1

    if not cfg.api_key:
        print("Error: NVIDIA_API_KEY is required (set env var or .env)")
        return 1

    try:
        if args.stream:
            model_id, chunk_iter, _attempts = await router.route_stream(req, allow_fallback=not args.no_fallback)
            print(f"[model] {model_id}")
            in_think = False
            async for chunk in chunk_iter:
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or choices[0].get("message") or {}
                # reasoning/thinking deltas
                rtext = None
                for k in ("reasoning_content", "reasoning", "thinking"):
                    v = delta.get(k)
                    if isinstance(v, str) and v:
                        rtext = v
                        break
                if rtext:
                    if not in_think:
                        print("\n[thinking]", file=sys.stderr)
                        in_think = True
                    print(rtext, end="", file=sys.stderr, flush=True)
                text = delta.get("content")
                if text:
                    if in_think:
                        print("\n[/thinking]\n", file=sys.stderr)
                        in_think = False
                    print(text, end="", flush=True)
            if in_think:
                print("\n[/thinking]", file=sys.stderr)
            print()
            return 0

        result = await router.route(req, allow_fallback=not args.no_fallback)
    except Exception as e:
        print(f"Routing/request failed: {e}")
        return 1
    finally:
        try:
            await router.aclose()
        except Exception:
            pass

    print(f"[model] {result.model_id}")
    if result.reasoning:
        print("[thinking]")
        print(result.reasoning)
        print("[/thinking]")
    if args.json:
        try:
            parsed = json.loads(result.content)
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
            return 0
        except Exception:
            pass
    print(result.content)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
