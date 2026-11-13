# router

NVIDIA **free-endpoint-only** router subproject for `mini-pi`.

- **Curated registry** of 44 NVIDIA Build free-endpoint models (`src/router/free_registry.py`) with usage categories, endpoint type, and speed/accuracy tier priors.
- **Chat model groups:** `nvidia-auto` (all chat-capable free endpoints) and `nvidia-fast` (low-latency, high-throughput chat endpoints, `speed_tier=1`) for optimal response speed/throughput.
- **Task-aware routing** (chat-completion): filters by task/endpoint/capability/context, ranks by **capability fit + accuracy + speed** (priors + observed telemetry), free-only fallback, deterministic cache, telemetry.
- **OpenAI-compatible service** (`--serve`) with **SSE streaming** (`stream: true`) so agents/clients can connect and chat through the router.
- **Optional LiteLLM config generation** from curated chat models.

## Setup

```bash
cd router
# optional
conda run -n aisystem python -m pip install -e .
```

Set API key (env or `.env` in `router/`):

```bash
export NVIDIA_API_KEY="nvapi-..."
# NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
# NVIDIA_FREE_MODELS=id1,id2   # optional allowlist of confirmed IDs
# NVIDIA_BLOCKED_MODELS=id3
```

## CLI usage

```bash
# list curated models / groups (all 44, nvidia-auto chat pool, nvidia-fast pool)
PYTHONPATH=src conda run -n aisystem python -m router.cli --list-models
PYTHONPATH=src conda run -n aisystem python -m router.cli --list-models --category nvidia-auto
PYTHONPATH=src conda run -n aisystem python -m router.cli --list-models --category nvidia-fast
PYTHONPATH=src conda run -n aisystem python -m router.cli --list-models --category coding

# dry-run: rank eligible models for a task/pool (no API call)
PYTHONPATH=src conda run -n aisystem python -m router.cli --rank --task-type coding --model nvidia-auto
PYTHONPATH=src conda run -n aisystem python -m router.cli --rank --task-type agents --model nvidia-fast

# route & chat (chat-completion endpoints)
PYTHONPATH=src conda run -n aisystem python -m router.cli --model nvidia-fast --task-type coding --stream "Write a Python function to add two numbers"
PYTHONPATH=src conda run -n aisystem python -m router.cli --model nvidia-auto --task-type agents "Plan steps to debug a failing test"
PYTHONPATH=src conda run -n aisystem python -m router.cli --model nvidia/nemotron-3.5-bolt-e4b "Hello"

# generate LiteLLM proxy config (optional, needs pyyaml/litellm extra)
PYTHONPATH=src conda run -n aisystem python -m router.cli --generate-litellm-config ./litellm-config.yaml
```

> If running via `conda run` without `PYTHONPATH`, install first (`conda run -n aisystem python -m pip install -e .`) or keep `PYTHONPATH=src`.

## Service mode (connect agents/clients)

```bash
PYTHONPATH=src conda run -n aisystem python -m router.cli --serve --host 0.0.0.0 --port 8000
```

Endpoints: `GET /health`, `GET /v1/models`, `POST /v1/chat/completions` (OpenAI-compatible, supports `stream: true`).

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia-fast","messages":[{"role":"user","content":"Write a Python function to reverse a string"}],"task_type":"coding"}'
```

`model`: `nvidia-auto` (all chat), `nvidia-fast` (fast chat), or a specific curated chat model id. Non-chat endpoints (embedding/rerank/parse/audio/other) are listed/classified but chat-completions routing uses chat-capable free endpoints.

## Design

- **Registry**: `free_registry.py` → `registry.py` (groups `nvidia-auto`/`nvidia-fast`, eligible by task/endpoint/capability/context)
- **Scoring**: task match + capability + accuracy tier + speed tier (blended with telemetry) + reliability/traffic/context
- **Router**: rank within pool, call NVIDIA `/v1/chat/completions`, telemetry, cache, fallback (free-only)
- **Server**: OpenAI-compatible `/v1/chat/completions` over the same router

Verify/pin model IDs against https://build.nvidia.com/models?filters=nimType%3Anim_type_preview via `NVIDIA_FREE_MODELS` if needed.
