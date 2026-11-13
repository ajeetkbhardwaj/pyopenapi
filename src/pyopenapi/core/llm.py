"""Optional LLM enrichment for OpenAPI specs.

Supports any OpenAI-compatible chat-completions endpoint:

* NVIDIA NIM (``https://integrate.api.nvidia.com/v1``)
* LM Studio (``http://127.0.0.1:1234/v1``)
* llama.cpp server (``http://127.0.0.1:8080/v1``)
* Ollama (``http://127.0.0.1:11434/v1``)
* any custom OpenAI-compatible server

The integration is dependency-light (``httpx`` only), sends credentials only via
the ``Authorization`` header, and never logs key material. Enrichment is
additive: it only fills human-facing fields (summaries, descriptions, tags) on
top of the deterministic static spec. Structural fields (paths, methods, schemas,
refs) are never rewritten.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Settings, get_settings
from ..logging_utils import get_logger
from ..models import LLMUsage

logger = get_logger("pyopenapi.llm")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_MAX_TEXT = 1200

_PROVIDER_ENGINES = {
    "nvidia",
    "openrouter",
    "lmstudio",
    "llamacpp",
    "ollama",
    "openai-compatible",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str | None = None
    spec_model: str | None = None
    context_model: str | None = None
    requires_key: bool = False
    auth_type: str = "bearer"  # "bearer", "raw", "none"


def nvidia_api_key() -> str | None:
    """Return the NVIDIA API key from the environment, if present."""

    return os.getenv("NVIDIA_API_KEY") or os.getenv("PYOPENAPI_NVIDIA_API_KEY")


def openrouter_api_key() -> str | None:
    """Return the OpenRouter API key from the environment, if present."""

    return os.getenv("OPENROUTER_API_KEY") or os.getenv("PYOPENAPI_OPENROUTER_API_KEY")


def nvidia_available() -> bool:
    if nvidia_api_key():
        return True
    # Ensure pyopenapi/.env is loaded before reporting absence.
    get_settings()
    return bool(nvidia_api_key())


def openrouter_available() -> bool:
    if openrouter_api_key():
        return True
    get_settings()
    return bool(openrouter_api_key())


def _ollama_base_url() -> str:
    host = os.getenv("OLLAMA_HOST")
    if not host:
        return "http://127.0.0.1:11434/v1"
    if host.startswith(("http://", "https://")):
        return host.rstrip("/") + ("" if host.rstrip("/").endswith("/v1") else "/v1")
    return f"http://{host.rstrip('/')}/v1"


def resolve_provider(settings: Settings | None = None, *, name: str | None = None) -> ProviderConfig | None:
    """Resolve the active provider from explicit config/environment.

    ``name`` forces a provider (used by ``engine="ollama"`` etc.). Without it,
    ``PYOPENAPI_LLM_PROVIDER`` is honoured, then NVIDIA (when a key is present),
    then Ollama (when ``OLLAMA_HOST`` is set).
    """

    settings = settings or get_settings()
    requested = (name or os.getenv("PYOPENAPI_LLM_PROVIDER") or "auto").strip().lower()
    base_override = os.getenv("PYOPENAPI_LLM_BASE_URL")
    key_override = os.getenv("PYOPENAPI_LLM_API_KEY")
    spec_override = os.getenv("PYOPENAPI_LLM_SPEC_MODEL")
    context_override = os.getenv("PYOPENAPI_LLM_CONTEXT_MODEL")

    if requested == "auto":
        # Prefer NVIDIA NIM (Nemotron family) as primary; fall back to any
        # available provider so local paths can still produce agent outcomes.
        preferred = (getattr(settings, "agent_preferred_provider", None) or os.getenv("PYOPENAPI_AGENT_PREFERRED_PROVIDER") or "nvidia").strip().lower()
        order = [preferred, "nvidia", "openrouter", "ollama"]
        seen = set()
        candidates = []
        for c in order:
            if c and c not in seen:
                candidates.append(c)
                seen.add(c)
        selected = None
        for candidate in candidates:
            if candidate == "nvidia" and nvidia_api_key():
                selected = "nvidia"
                break
            if candidate == "openrouter" and openrouter_api_key():
                selected = "openrouter"
                break
            if candidate == "ollama" and os.getenv("OLLAMA_HOST"):
                selected = "ollama"
                break
        requested = selected or "none"
        if requested == "none":
            return None

    if requested == "nvidia":
        return ProviderConfig(
            name="nvidia",
            base_url=base_override or settings.nvidia_base_url,
            api_key=key_override or nvidia_api_key(),
            spec_model=spec_override or settings.nvidia_spec_model,
            context_model=context_override or settings.nvidia_context_model,
            requires_key=True,
            auth_type="bearer",
        )

    if requested == "openrouter":
        return ProviderConfig(
            name="openrouter",
            base_url=base_override
            or os.getenv("PYOPENAPI_OPENROUTER_BASE_URL")
            or settings.openrouter_base_url,
            api_key=key_override or openrouter_api_key(),
            spec_model=spec_override
            or os.getenv("PYOPENAPI_OPENROUTER_SPEC_MODEL")
            or os.getenv("OPENROUTER_MODEL")
            or settings.openrouter_spec_model,
            context_model=context_override or settings.openrouter_context_model,
            requires_key=True,
            auth_type="bearer",
        )

    presets = {
        "lmstudio": os.getenv("PYOPENAPI_LMSTUDIO_BASE_URL") or "http://127.0.0.1:1234/v1",
        "llamacpp": os.getenv("PYOPENAPI_LLAMACPP_BASE_URL")
        or os.getenv("PYOPENAPI_LLAMA_CPP_BASE_URL")
        or "http://127.0.0.1:8080/v1",
        "ollama": os.getenv("PYOPENAPI_OLLAMA_BASE_URL") or _ollama_base_url(),
    }
    if requested in presets or requested in {"openai-compatible", "openai"}:
        base_url = base_override or presets.get(requested, "")
        if not base_url:
            return None
        api_key = key_override
        if requested in {"openai-compatible", "openai"} and not api_key:
            api_key = os.getenv("OPENAI_API_KEY")
        return ProviderConfig(
            name="openai-compatible" if requested == "openai" else requested,
            base_url=base_url,
            api_key=api_key,
            spec_model=spec_override,
            context_model=context_override,
            requires_key=False,
        )

    return None


def llm_available() -> bool:
    return resolve_provider() is not None


def _headers(provider: ProviderConfig) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if provider.api_key:
        if provider.auth_type == "raw":
            headers["Authorization"] = provider.api_key
        elif provider.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {provider.api_key}"
        # auth_type == "none" -> no auth header
    if provider.name == "openrouter":
        headers["HTTP-Referer"] = os.getenv(
            "PYOPENAPI_OPENROUTER_SITE_URL", "https://github.com/knowl-apidocs"
        )
        headers["X-Title"] = os.getenv("PYOPENAPI_OPENROUTER_APP_NAME", "pyopenapi")
    return headers


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text.strip())


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _strip_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


_SYSTEM_PROMPT = (
    "You are a precise API documentation editor. You receive a machine-generated "
    "OpenAPI summary and must improve only human-facing text. Return a single JSON "
    "object and no prose. Never invent endpoints, fields, parameters or schemas. "
    "Keep summaries under 80 characters and descriptions under 400 characters."
)


def _operation_brief(method: str, path: str, operation: dict[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "method": method.upper(),
        "path": path,
        "operationId": operation.get("operationId"),
        "summary": operation.get("summary"),
        "tags": operation.get("tags"),
    }
    parameters = operation.get("parameters")
    if isinstance(parameters, list):
        brief["parameters"] = [
            {
                "name": item.get("name"),
                "in": item.get("in"),
                "required": item.get("required"),
                "type": (item.get("schema") or {}).get("type"),
            }
            for item in parameters
            if isinstance(item, dict)
        ]
    request = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if isinstance(request, dict):
        brief["requestSchema"] = request.get("$ref") or request.get("type")
    responses = operation.get("responses") or {}
    if isinstance(responses, dict):
        for status, response in responses.items():
            schema = (
                (response or {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if isinstance(schema, dict):
                brief["responseSchema"] = schema.get("$ref") or schema.get("type")
                brief["responseStatus"] = status
                break
    return brief


def _component_brief(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    fields: list[dict[str, Any]] = []
    if isinstance(properties, dict):
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            fields.append(
                {
                    "name": field_name,
                    "type": field_schema.get("type") or ("ref" if "$ref" in field_schema else None),
                    "format": field_schema.get("format"),
                    "required": field_name in (schema.get("required") or []),
                }
            )
    return {"name": name, "fields": fields}


def _build_prompt(
    title: str,
    operations: list[dict[str, Any]],
    components: list[dict[str, Any]],
) -> str:
    payload = {"title": title, "operations": operations, "components": components}
    return (
        "Improve the documentation text for the following API. Return JSON shaped as:\n"
        '{"operations": {"<METHOD> <path>": {"summary": "...", "description": "...", '
        '"tags": ["..."]}}, "components": {"<Name>": {"description": "...", '
        '"fields": {"<field>": {"description": "..."}}}}}\n\n'
        "Only include keys you are confident about. Omit anything else.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


async def _discover_model(client: httpx.AsyncClient, provider: ProviderConfig) -> str | None:
    try:
        response = await client.get(
            provider.base_url.rstrip("/") + "/models", headers=_headers(provider)
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = data.get("models") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    ids: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("id") or item.get("name") or item.get("model")
            if isinstance(value, str) and value:
                ids.append(value)
    for model_id in ids:
        lowered = model_id.lower()
        if "embed" not in lowered and "rerank" not in lowered:
            return model_id
    return ids[0] if ids else None


async def _post_chat(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    *,
    model: str,
    prompt: str,
    max_tokens: int = 1800,
    usage: LLMUsage | None = None,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if provider.name == "openrouter":
        try:
            floor = int(os.getenv("PYOPENAPI_OPENROUTER_MAX_TOKENS", "4096"))
        except ValueError:
            floor = 4096
        payload["max_tokens"] = max(max_tokens, floor)
        payload["reasoning"] = {
            "effort": os.getenv("PYOPENAPI_OPENROUTER_REASONING_EFFORT", "low")
        }
    elif provider.name == "nvidia":
        model_l = (model or "").lower()
        if "nemotron" in model_l:
            try:
                floor = int(os.getenv("PYOPENAPI_NVIDIA_MAX_TOKENS", "4096"))
            except ValueError:
                floor = 4096
            payload["max_tokens"] = max(max_tokens, floor)
    url = provider.base_url.rstrip("/") + "/chat/completions"

    response = await client.post(url, headers=_headers(provider), json=payload)
    if response.status_code in {400, 422}:
        # Some local servers do not accept response_format; retry once without it.
        payload.pop("response_format", None)
        response = await client.post(url, headers=_headers(provider), json=payload)
    if response.status_code in {400, 422} and "reasoning" in payload:
        # Some OpenRouter models do not accept the reasoning hint.
        payload.pop("reasoning", None)
        response = await client.post(url, headers=_headers(provider), json=payload)
    if response.status_code == 401:
        raise RuntimeError(f"LLM authentication failed (401): {response.text[:200]}")
    response.raise_for_status()
    data = response.json()
    if usage is not None:
        _record_usage(usage, data.get("usage"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM response contained no choices")
    message = choices[0].get("message") or {}
    # NVIDIA Nemotron models may return content in reasoning_content instead of content
    content = message.get("content") or message.get("reasoning_content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM response contained empty content")
    return content


def _record_usage(usage: LLMUsage, raw: Any) -> None:
    usage.requests += 1
    if not isinstance(raw, dict):
        return
    prompt_tokens = raw.get("prompt_tokens")
    completion_tokens = raw.get("completion_tokens")
    total_tokens = raw.get("total_tokens")
    if isinstance(prompt_tokens, int):
        usage.prompt_tokens += prompt_tokens
    if isinstance(completion_tokens, int):
        usage.completion_tokens += completion_tokens
    if isinstance(total_tokens, int):
        usage.total_tokens += total_tokens
    elif isinstance(prompt_tokens, int) or isinstance(completion_tokens, int):
        usage.total_tokens += (prompt_tokens or 0) + (completion_tokens or 0)
    if usage.budget and usage.total_tokens > usage.budget:
        usage.budget_exceeded = True


def _merge_operations(spec: dict[str, Any], patch: dict[str, Any]) -> int:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return 0
    changed = 0
    for key, update in (patch.get("operations") or {}).items():
        if not isinstance(update, dict) or " " not in str(key):
            continue
        method, path = str(key).split(" ", 1)
        operation = (paths.get(path) or {}).get(method.lower())
        if not isinstance(operation, dict):
            continue
        for field in ("summary", "description"):
            value = update.get(field)
            if isinstance(value, str) and value.strip():
                operation[field] = value.strip()[:_MAX_TEXT]
                changed += 1
        tags = update.get("tags")
        if isinstance(tags, list):
            cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
            if cleaned:
                operation["tags"] = cleaned[:8]
                changed += 1
    return changed


def _merge_components(spec: dict[str, Any], patch: dict[str, Any]) -> int:
    schemas = (spec.get("components") or {}).get("schemas")
    if not isinstance(schemas, dict):
        return 0
    changed = 0
    for name, update in (patch.get("components") or {}).items():
        schema = schemas.get(name)
        if not isinstance(schema, dict) or not isinstance(update, dict):
            continue
        description = update.get("description")
        if isinstance(description, str) and description.strip():
            schema["description"] = description.strip()[:_MAX_TEXT]
            changed += 1
        field_updates = update.get("fields")
        properties = schema.get("properties")
        if isinstance(field_updates, dict) and isinstance(properties, dict):
            for field_name, field_update in field_updates.items():
                field_schema = properties.get(field_name)
                if not isinstance(field_schema, dict) or not isinstance(field_update, dict):
                    continue
                field_description = field_update.get("description")
                if isinstance(field_description, str) and field_description.strip():
                    field_schema["description"] = field_description.strip()[:_MAX_TEXT]
                    changed += 1
    return changed


async def _call_post_chat(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    *,
    model: str,
    prompt: str,
    max_tokens: int = 1800,
    usage: LLMUsage | None = None,
) -> str:
    """Call ``_post_chat``, tolerating older stubs that lack ``usage``."""

    kwargs: dict[str, Any] = {"model": model, "prompt": prompt, "max_tokens": max_tokens}
    if usage is not None:
        try:
            return await _post_chat(client, provider, usage=usage, **kwargs)
        except TypeError:
            # Backward compatibility with injected/fake implementations.
            pass
    return await _post_chat(client, provider, **kwargs)


async def _enrich_chunk(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    model: str,
    title: str,
    operations: list[dict[str, Any]],
    components: list[dict[str, Any]],
    *,
    usage: LLMUsage | None = None,
) -> dict[str, Any] | None:
    prompt = _build_prompt(title, operations, components)
    try:
        text = await _call_post_chat(
            client, provider, model=model, prompt=prompt, usage=usage
        )
    except Exception as exc:  # network/HTTP/parse errors are non-fatal
        logger.warning("LLM enrichment chunk failed: %s", exc)
        return None
    return _parse_json_object(text)


async def enrich_openapi_with_llm(
    spec: dict[str, Any],
    *,
    settings: Settings | None = None,
    provider: ProviderConfig | None = None,
    provider_name: str | None = None,
    spec_model: str | None = None,
    context_model: str | None = None,
    usage: LLMUsage | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return a copy of ``spec`` with LLM-generated summaries/descriptions.

    Failures are reported as warnings; the deterministic spec is always usable.
    The input document is never mutated. When ``usage`` is supplied it is filled
    with token counts, and ``settings.llm_max_tokens_budget`` (0 = unlimited) is
    enforced across requests.
    """

    settings = settings or get_settings()
    warnings: list[str] = []
    provider = provider or resolve_provider(settings, name=provider_name)
    if provider is None:
        warnings.append(
            "LLM enrichment skipped: no provider configured. Set NVIDIA_API_KEY, "
            "OLLAMA_HOST, PYOPENAPI_LLM_PROVIDER or PYOPENAPI_LLM_BASE_URL."
        )
        return spec, warnings
    if provider.requires_key and not provider.api_key:
        warnings.append(f"LLM enrichment skipped: no API key configured for {provider.name}.")
        return spec, warnings

    spec = copy.deepcopy(spec)
    if usage is None:
        usage = LLMUsage()
    usage.budget = int(getattr(settings, "llm_max_tokens_budget", 0) or 0)

    operations: list[dict[str, Any]] = []
    paths = spec.get("paths")
    if isinstance(paths, dict):
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}:
                    if isinstance(operation, dict):
                        operations.append(_operation_brief(method, path, operation))

    components: list[dict[str, Any]] = []
    schemas = (spec.get("components") or {}).get("schemas")
    if isinstance(schemas, dict):
        components = [_component_brief(name, schema) for name, schema in schemas.items() if isinstance(schema, dict)]

    if not operations and not components:
        return spec, warnings

    title = str((spec.get("info") or {}).get("title") or "API")
    chunk_size = max(1, settings.llm_endpoints_per_request)
    chunks = [operations[index : index + chunk_size] for index in range(0, len(operations), chunk_size)]
    if not chunks:
        chunks = [[]]
    chunks[0] = chunks[0] + components

    semaphore = asyncio.Semaphore(max(1, settings.llm_max_concurrency))
    timeout = httpx.Timeout(settings.llm_timeout_seconds)
    changed = 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        model = spec_model or provider.spec_model
        if not model:
            model = await _discover_model(client, provider)
        if not model:
            warnings.append(
                f"LLM enrichment skipped: no model configured for {provider.name}. "
                "Set PYOPENAPI_LLM_SPEC_MODEL or PYOPENAPI_SPEC_MODEL."
            )
            return spec, warnings

        async def run(chunk: list[dict[str, Any]]) -> dict[str, Any] | None:
            async with semaphore:
                if usage.budget and usage.total_tokens >= usage.budget:
                    usage.budget_exceeded = True
                    return None
                ops = [item for item in chunk if "method" in item]
                comps = [item for item in chunk if "fields" in item]
                return await _enrich_chunk(client, provider, model, title, ops, comps, usage=usage)

        results = await asyncio.gather(*(run(chunk) for chunk in chunks), return_exceptions=True)

    if usage.budget_exceeded:
        warnings.append(
            f"LLM token budget of {usage.budget} was reached after {usage.total_tokens} tokens; "
            "remaining enrichment requests were skipped."
        )

    for result in results:
        if isinstance(result, Exception) or result is None:
            continue
        changed += _merge_operations(spec, result)
        changed += _merge_components(spec, result)

    if changed == 0:
        warnings.append(f"{provider.name} enrichment returned no usable changes; kept the static spec.")
    else:
        warnings.append(f"{provider.name} enrichment applied {changed} documentation update(s) using {model}.")

    # Optional short overview from the faster context model (falls back to spec model).
    overview_model = context_model or provider.context_model or model
    if overview_model:
        overview_prompt = (
            "Write a 2-3 sentence overview of this API for developers. Return JSON "
            '{"description": "..."} and nothing else. Paths: '
            + json.dumps(sorted(paths.keys()) if isinstance(paths, dict) else [], ensure_ascii=False)
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                text = await _call_post_chat(
                    client,
                    provider,
                    model=overview_model,
                    prompt=overview_prompt,
                    max_tokens=300,
                    usage=usage,
                )
            parsed = _parse_json_object(text) or {}
            description = parsed.get("description")
            if isinstance(description, str) and description.strip():
                spec.setdefault("info", {})["description"] = description.strip()[:_MAX_TEXT]
        except Exception as exc:
            warnings.append(f"{provider.name} overview skipped: {type(exc).__name__}.")

    return spec, warnings


async def enrich_openapi_with_nvidia(
    spec: dict[str, Any],
    **kwargs: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Backward-compatible alias for NVIDIA-specific enrichment."""

    kwargs.setdefault("provider_name", "nvidia")
    return await enrich_openapi_with_llm(spec, **kwargs)
