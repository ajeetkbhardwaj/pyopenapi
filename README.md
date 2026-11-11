# pyopenapi

An MCP server that turns Python web-framework source code into **OpenAPI 3.0/3.1**
specifications and **Markdown** documentation. It is designed for coding agents
(OpenCode, Claude Code, Cursor, etc.) and can run locally over stdio or as a
shared HTTP service.

Supported frameworks:

| Framework | Status | Endpoints | Schema components | Spec engines |
| --- | --- | --- | --- | --- |
| Django / DRF | stable | AST + optional runtime URLconf | serializers, models, enums | static + LLM providers + legacy LLM |
| Flask / Flask-RESTful | preview | static AST | Pydantic, Marshmallow, enums | static + LLM providers |
| FastAPI | preview | static AST | Pydantic, enums | static + LLM providers |

The deterministic `static` engine needs no LLM credentials and works in sandboxes
and CI. Any OpenAI-compatible provider — NVIDIA NIM, LM Studio, llama.cpp or
Ollama — can enrich that static spec with summaries and descriptions. The legacy
`llm` engine reuses the upstream knowl-apidocs pipeline for Django only.

## Install

```bash
# from this directory
pip install -e pyopenapi

# with dev dependencies
pip install -e "pyopenapi[dev]"
```

The package entry points are `pyopenapi` and `pyopenapi-mcp` (both run the same
CLI). Python 3.10+ is required.

## Quick start

```bash
# Analyze a local project and cache the result
pyopenapi analyze /path/to/project

# Analyze a GitHub repository (shallow-cloned into the cache)
pyopenapi analyze https://github.com/owner/repo

# Generate an OpenAPI spec (static, no credentials required)
pyopenapi generate /path/to/project --output ./out

# Re-clone a GitHub URL to pick up the latest commits
pyopenapi generate https://github.com/owner/repo --refresh-repo --output ./out

# Generate Markdown from the cached analysis
pyopenapi markdown <analysis_id> --output ./docs

# Validate a spec
pyopenapi validate ./out/openapi.yml

# Run the MCP server over stdio (default)
pyopenapi serve

# Run the MCP server over HTTP
pyopenapi serve --transport streamable-http --host 0.0.0.0 --port 8765
```

With no subcommand, `pyopenapi` serves over stdio, so it can be used directly as
an MCP command.

## MCP client configuration

### Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "pyopenapi": {
      "command": "pyopenapi",
      "args": ["serve"],
      "env": {
        "PYOPENAPI_CACHE_DIR": "/absolute/path/to/cache"
      }
    }
  }
}
```

### OpenCode

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "pyopenapi": {
      "type": "local",
      "command": ["pyopenapi", "serve"],
      "enabled": true
    }
  }
}
```

### Remote HTTP

Start the server with `pyopenapi serve --transport streamable-http`, then point
the client at `http://<host>:<port>/mcp` (or `/sse` for the legacy SSE
transport).

HTTP transports add optional bearer-token auth (`PYOPENAPI_API_KEY`), per-IP
rate limiting, request body-size limits, request timeouts and operational
endpoints: `GET /health` (and `/healthz`, `/ready`, `/readyz`) plus Prometheus
metrics at `GET /metrics` (JSON at `/metrics.json`). Health endpoints stay
public; everything else requires the token when one is configured.

## MCP tools

| Tool | Description |
| --- | --- |
| `list_frameworks` | Supported frameworks, status and capabilities. |
| `detect_framework` | Detect Django/Flask/FastAPI from a project path. |
| `analyze_project` | Analyze a project and cache endpoints/components. |
| `analyze_workspace` | Analyze every project in a workspace and optionally merge specs. |
| `get_analysis` | Return a cached analysis summary. |
| `list_analyses` | List cached analyses, newest first. |
| `delete_analysis` | Delete a cached analysis and its artifacts. |
| `cache_stats` | Cache size/age report with per-analysis entries. |
| `cleanup_cache` | Prune old/large analyses (supports `dry_run`). |
| `extract_endpoints` | Filtered, paginated endpoint list. |
| `extract_components` | Filtered, paginated schema component list. |
| `generate_openapi` | Generate an OpenAPI 3.0/3.1 spec (`auto`, `static`, `llm`, providers). |
| `generate_markdown` | Generate Markdown from an analysis or spec. |
| `validate_openapi` | Structural OpenAPI validation (supports 3.1 webhooks). |
| `merge_specs` | Merge multiple specs (paths or analysis ids). |
| `diff_specs` | Compare specs and flag breaking changes. |
| `generate_docs` | One-shot analyze → spec → Markdown workflow. |

Long-running tools report progress through the MCP progress channel and validate
enum-like arguments (framework, engine, format, layout) before running.

Typical agent workflow:

1. `analyze_project(project_path)`
2. `extract_endpoints(analysis_id)` / `extract_components(analysis_id)`
3. `generate_openapi(analysis_id, engine="static")`
4. `generate_markdown(spec_path=...)`
5. `validate_openapi(spec_path=...)`

## MCP resources

| URI | Content |
| --- | --- |
| `pyopenapi://frameworks` | Supported frameworks. |
| `pyopenapi://analyses` | Cached analyses. |
| `pyopenapi://analysis/{id}` | Analysis summary. |
| `pyopenapi://analysis/{id}/endpoints` | Endpoints. |
| `pyopenapi://analysis/{id}/endpoints/{offset}/{limit}` | Paginated endpoints (max 5000/page). |
| `pyopenapi://analysis/{id}/components` | Components. |
| `pyopenapi://analysis/{id}/components/{offset}/{limit}` | Paginated components (max 5000/page). |
| `pyopenapi://analysis/{id}/openapi` | Static OpenAPI spec (configured version). |
| `pyopenapi://analysis/{id}/markdown` | Markdown index. |
| `pyopenapi://analysis/{id}/warnings` | Analysis warnings. |

## CLI

```
pyopenapi serve [--transport stdio|sse|streamable-http] [--host H] [--port P]
pyopenapi detect PROJECT
pyopenapi analyze PROJECT [--framework auto|django|flask|fastapi] [--dynamic] [--force] [--refresh-repo]
pyopenapi list
pyopenapi generate PROJECT [--engine auto|static|nvidia|lmstudio|llamacpp|ollama|openai-compatible|llm] [--format yml|yaml|json] [--output DIR] [--openapi-version 3.0.3|3.1.0] [--refresh-repo]
pyopenapi markdown SOURCE [--layout multi|single] [--output DIR]
pyopenapi validate SOURCE
pyopenapi cache [--cleanup] [--max-age-days N] [--max-bytes N] [--dry-run] [--include-repos]
pyopenapi merge SPEC [SPEC ...] [--title T] [--version V] [--output DIR] [--format yml|yaml|json]
pyopenapi diff OLD NEW
pyopenapi workspace DIR [--framework auto|django|flask|fastapi] [--no-merge] [--output DIR]
```

`PROJECT` is a local project path or a GitHub URL/shorthand
(`https://github.com/owner/repo`, `git@github.com:owner/repo.git`,
`github:owner/repo`). GitHub sources are shallow-cloned into
`$PYOPENAPI_CACHE_DIR/repos/` and reused; pass `--refresh-repo` to re-clone.
`SOURCE` is either a cached analysis id or a path to an OpenAPI file.

## Configuration

All settings use the `PYOPENAPI_` prefix and can be set in the environment or a
`.env` file. See `.env.example`.

| Variable | Default | Description |
| --- | --- | --- |
| `PYOPENAPI_TRANSPORT` | `stdio` | `stdio`, `sse` or `streamable-http`. |
| `PYOPENAPI_HOST` | `127.0.0.1` | HTTP bind host. |
| `PYOPENAPI_PORT` | `8765` | HTTP bind port. |
| `PYOPENAPI_CACHE_DIR` | `~/.cache/pyopenapi` | Analysis cache root. |
| `PYOPENAPI_KNOWL_ROOT` | auto | knowl-apidocs checkout for the LLM engine. |
| `PYOPENAPI_MAX_SCAN_FILES` | `20000` | File scan limit. |
| `PYOPENAPI_MAX_FILE_BYTES` | `2000000` | Per-file size limit. |
| `PYOPENAPI_DEFAULT_USE_DYNAMIC` | `false` | Try Django runtime URLconf extraction first. |
| `PYOPENAPI_LLM_PROVIDER` | `auto` | `auto`, `nvidia`, `lmstudio`, `llamacpp`, `ollama`, `openai-compatible`. |
| `PYOPENAPI_LLM_BASE_URL` | unset | Override the provider's OpenAI-compatible base URL. |
| `PYOPENAPI_LLM_API_KEY` | unset | Override the provider API key (local providers need none). |
| `PYOPENAPI_LLM_SPEC_MODEL` | auto | Model for enrichment; auto-discovered via `/models` if unset. |
| `PYOPENAPI_LLM_CONTEXT_MODEL` | spec model | Model for the API overview. |
| `PYOPENAPI_LLM_TIMEOUT_SECONDS` | `120` | Per-request timeout. |
| `PYOPENAPI_LLM_MAX_CONCURRENCY` | `4` | Parallel enrichment requests. |
| `PYOPENAPI_LLM_ENDPOINTS_PER_REQUEST` | `40` | Endpoint batch size per request. |
| `PYOPENAPI_NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA NIM base URL. |
| `PYOPENAPI_NVIDIA_SPEC_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b` | NVIDIA enrichment model. |
| `PYOPENAPI_NVIDIA_CONTEXT_MODEL` | `nvidia/nemotron-3.5-lightning-30b-a3b` | NVIDIA overview model. |
| `PYOPENAPI_LMSTUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | LM Studio base URL. |
| `PYOPENAPI_LLAMACPP_BASE_URL` | `http://127.0.0.1:8080/v1` | llama.cpp server base URL. |
| `PYOPENAPI_OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` | Ollama base URL (or use `OLLAMA_HOST`). |
| `PYOPENAPI_SPEC_MODEL` | unset | Legacy LLM model override. |
| `PYOPENAPI_CONTEXT_MODEL` | unset | Legacy LLM context model override. |
| `PYOPENAPI_LOG_LEVEL` | `INFO` | Log level (logs always go to stderr). |
| `PYOPENAPI_LOG_JSON` | `false` | Emit one JSON log object per line. |
| `PYOPENAPI_API_KEY` | unset | Bearer token required for HTTP transports (health stays public). |
| `PYOPENAPI_ENABLE_HEALTH_ENDPOINTS` | `true` | Serve `/health`, `/healthz`, `/ready`, `/readyz`. |
| `PYOPENAPI_ENABLE_METRICS_ENDPOINT` | `true` | Serve Prometheus metrics at `/metrics`. |
| `PYOPENAPI_METRICS_PATH` | `/metrics` | Metrics route path. |
| `PYOPENAPI_MAX_REQUEST_BYTES` | `10000000` | Reject larger HTTP request bodies. |
| `PYOPENAPI_REQUEST_TIMEOUT_SECONDS` | `1800` | Per-request HTTP timeout (0 disables). |
| `PYOPENAPI_RATE_LIMIT_PER_MINUTE` | `0` | Per-IP requests/minute (0 disables). |
| `PYOPENAPI_RATE_LIMIT_BURST` | `20` | Extra burst allowance for the rate limiter. |
| `PYOPENAPI_TRUSTED_PROXIES` | unset | Proxy IPs allowed to set `X-Forwarded-For` (JSON list or CSV). |
| `PYOPENAPI_LOCK_TIMEOUT_SECONDS` | `900` | Max wait for a concurrent analysis lock. |
| `PYOPENAPI_CACHE_MAX_AGE_DAYS` | `30` | Default prune age for `cleanup_cache` (0 disables). |
| `PYOPENAPI_CACHE_MAX_BYTES` | `5000000000` | Default prune size cap (0 disables). |
| `PYOPENAPI_OPENAPI_VERSION` | `3.0.3` | Output dialect: `3.0.3` or `3.1.0`. |
| `PYOPENAPI_LLM_MAX_TOKENS_BUDGET` | `0` | Stop enrichment after N total tokens (0 disables). |
| `PYOPENAPI_GITHUB_TOKEN` | unset | HTTPS token for private GitHub clones (or `GITHUB_TOKEN`). |
| `PYOPENAPI_GITHUB_SSH_KEY` | unset | Private key path for SSH GitHub clones. |

Provider credentials: NVIDIA uses `NVIDIA_API_KEY` (or
`PYOPENAPI_NVIDIA_API_KEY`); local providers need no key. Keys are read from the
environment/`.env` and sent only in the `Authorization` header; they are never
logged. `.env` files are gitignored.

Local providers must already be running with a model loaded:

```bash
# LM Studio (server enabled in the app)
PYOPENAPI_LLM_PROVIDER=lmstudio PYOPENAPI_LLM_SPEC_MODEL=<model-id> pyopenapi generate PROJECT

# llama.cpp server
PYOPENAPI_LLM_PROVIDER=llamacpp PYOPENAPI_LLM_SPEC_MODEL=<model-id> pyopenapi generate PROJECT

# Ollama
PYOPENAPI_LLM_PROVIDER=ollama PYOPENAPI_LLM_SPEC_MODEL=llama3.1 pyopenapi generate PROJECT
```

Legacy LLM credentials (only for `engine="llm"`): `AZURE_CONFIG_NAMES` +
`AZURE_ENDPOINT_*` / `AZURE_API_KEY_*` / `AZURE_MODEL_MAP_*`, or
`GOOGLE_API_KEY`, or `DEEPSEEK_API_KEY`.

## How it works

```
project source
   │
   ├─ detection.py            weighted import/decorator signals
   │
   ├─ analyzers/              Django (legacy AST + static fallback),
   │                          Flask (AST), FastAPI (AST), schema extractors
   │
   ├─ core/analysis.py        cache, locks, filtering, summaries
   ├─ core/spec.py            static builder + provider/legacy LLM engines
   ├─ core/llm.py             OpenAI-compatible enrichment (httpx, no key logging)
   ├─ core/markdown.py        OpenAPI → Markdown
   └─ core/validation.py      structural checks and $ref resolution
```

Analysis artifacts are cached under
`$PYOPENAPI_CACHE_DIR/analyses/<analysis_id>/`:

```
analysis.json      summary
endpoints.json     raw endpoints
components.json    raw components
metadata.json      analyzer metadata
work/              analyzer scratch space
.lock              inter-process lock
```

### Static vs dynamic vs LLM providers vs legacy LLM

- **static** (default without credentials): deterministic AST extraction. No
  project imports, no network, safe for untrusted code and CI.
- **dynamic** (Django only): runs the project's URLconf in a subprocess. Requires
  Django and the project's dependencies in the server interpreter. Falls back to
  static automatically.
- **LLM providers** (all frameworks): `nvidia`, `lmstudio`, `llamacpp`, `ollama`
  and `openai-compatible` build the static spec, then ask the provider to improve
  only human-facing fields (summaries, descriptions, tags). Structural fields are
  never rewritten; network/API failures keep the static spec. `engine="auto"`
  selects NVIDIA when a key is set, otherwise Ollama when `OLLAMA_HOST` is set.
- **llm** (Django only): reuses the upstream knowl-apidocs LLM pipeline. Requires
  Azure/Gemini/DeepSeek credentials and can be slow/costly. Use `engine="static"`
  or a local provider for reproducible/portable output.

## Current limitations

- Django dynamic extraction requires the analysed project's dependencies to be
  importable by the server; otherwise static extraction is used and the summary
  records `degraded`/`used_dynamic` so agents can tell which path ran.
- Flask and FastAPI use static extraction only. FastAPI's runtime
  `/openapi.json` export is not used yet.
- Static FastAPI extraction understands `@app.<verb>`, `@router.<verb>`,
  `api_route`, `include_router(prefix=...)`, Pydantic request/response models,
  and `Query`/`Path`/`Header`/`Cookie`/`Form`/`File`/`Body` parameters
  (including `Annotated[...]`), plus `Depends`/`Security` with inferred security
  schemes.
- Flask extraction understands `@app.<verb>`, `@bp.<verb>`, `add_url_rule`,
  `MethodView`, `flask_restful` `add_resource`, `flask-smorest`
  (`@bp.arguments`/`@bp.response`) and `flask-restx`
  (`Namespace`, `@ns.route`, `@ns.marshal_with`/`@ns.expect`).
- Legacy LLM generation supports Django only; OpenAI-compatible enrichment works
  for all supported frameworks.
- The static builder emits conservative request/response schemas (objects or
  model refs); LLM enrichment only rewrites human-facing text, never structure.

## Development

```bash
cd pyopenapi
python -m pytest
python -m compileall -q src
```

The test suite uses synthetic Django/Flask/FastAPI projects and does not require
Django, Flask or FastAPI to be installed.

## Project layout

```
pyopenapi/
├── pyproject.toml
├── .env.example
├── src/pyopenapi/
│   ├── cli.py                 CLI and default stdio serve
│   ├── config.py              pydantic-settings configuration
│   ├── detection.py           framework detection
│   ├── models.py              pydantic response models
│   ├── bootstrap.py           locate knowl-apidocs + dotenv
│   ├── utils.py               stdout guard, locks, atomic writes
│   ├── analyzers/             Django/Flask/FastAPI + schema extractors
│   ├── core/                  analysis cache, spec, Markdown, validation
│   └── server/                FastMCP instance, tools, resources, app
└── tests/
```
