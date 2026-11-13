"""OpenAPI spec generation.

Engines:

* ``agent`` - the agent-native pipeline: IR + per-endpoint/component reasoning
  + deterministic assembly. This is the default when an LLM provider is
  configured.
* ``llm`` - the upstream knowl-apidocs LLM pipeline (Django, richest output)
* ``static`` - a deterministic builder from cached analysis (all frameworks)

``engine="auto"`` selects the agent pipeline when a provider is configured and
falls back to the deterministic static builder with a warning when no provider
is available. Pass ``engine="static"`` to always skip the LLM.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from ..bootstrap import ensure_knowl_on_path
from ..config import get_settings
from ..errors import (
    FrameworkNotSupportedError,
    LLMNotConfiguredError,
    SpecGenerationError,
)
from ..logging_utils import get_logger
from ..models import DiffChange, DiffResult, GenerationResult, LLMUsage
from ..utils import (
    Stopwatch,
    atomic_write_text,
    ensure_directory,
    redirect_stdout_to_stderr,
    slugify,
)
from .analysis import AnalysisRecord
from .llm import enrich_openapi_with_llm, nvidia_available

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
_PROVIDER_ENGINES = {"nvidia", "openrouter", "lmstudio", "llamacpp", "ollama", "openai-compatible"}


def legacy_llm_available() -> bool:
    return bool(
        os.getenv("AZURE_CONFIG_NAMES")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
    )


# ---------------------------------------------------------------- static build
def _endpoint_path(raw: dict[str, Any]) -> str:
    url = raw.get("url")
    if isinstance(url, dict):
        return str(url.get("url") or "")
    return str(url or "")


_PARAM_LOCATIONS = {"path", "query", "header", "cookie"}


def _endpoint_parameters(raw: dict[str, Any]) -> list[dict[str, Any]]:
    url = raw.get("url")
    parameters = url.get("parameter", []) if isinstance(url, dict) else []
    result: list[dict[str, Any]] = []
    for item in parameters or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        location = str(item.get("location", "query"))
        if location not in _PARAM_LOCATIONS:
            continue
        result.append(
            {
                "name": item["name"],
                "in": location,
                "required": bool(item.get("required", location == "path")),
                "schema": {"type": item.get("type") or "string"},
                **({"description": item["description"]} if item.get("description") else {}),
            }
        )
    return result


def _form_request_body(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Build a multipart/form-data requestBody from Form()/File() params."""

    url = raw.get("url")
    parameters = url.get("parameter", []) if isinstance(url, dict) else []
    properties: dict[str, Any] = {}
    required: list[str] = []
    has_file = False
    for item in parameters or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        location = item.get("location")
        if location not in {"form", "file"}:
            continue
        field_type = item.get("type") or "string"
        schema: dict[str, Any] = (
            {"type": "string", "format": "binary"} if location == "file" else {"type": field_type}
        )
        properties[str(item["name"])] = schema
        if location == "file":
            has_file = True
        if item.get("required"):
            required.append(str(item["name"]))
    if not properties:
        return None
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    content_type = "multipart/form-data" if has_file else "application/x-www-form-urlencoded"
    return {"required": bool(required), "content": {content_type: {"schema": schema}}}


def _inline_json_body(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Build a JSON requestBody from Body(...) annotated scalar/object params."""

    url = raw.get("url")
    parameters = url.get("parameter", []) if isinstance(url, dict) else []
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in parameters or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        if item.get("location") != "body":
            continue
        properties[str(item["name"])] = {"type": item.get("type") or "string"}
        if item.get("required"):
            required.append(str(item["name"]))
    if not properties:
        return None
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"required": bool(required), "content": {"application/json": {"schema": schema}}}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _apply_nullable(schema: dict[str, Any], openapi_version: str) -> dict[str, Any]:
    """Translate the internal nullable flag into the target OpenAPI dialect.

    OpenAPI 3.0 uses ``nullable: true``; OpenAPI 3.1 (JSON Schema 2020-12)
    expresses nullability through a type union (``type: [<t>, "null"]``).
    """

    if not schema.pop("nullable", False):
        return schema
    if not openapi_version.startswith("3.1"):
        schema["nullable"] = True
        return schema

    type_name = schema.get("type")
    if isinstance(type_name, str):
        schema["type"] = [type_name, "null"]
    elif isinstance(type_name, list):
        if "null" not in type_name:
            schema["type"] = [*type_name, "null"]
    elif "$ref" in schema:
        # 3.1 allows sibling keywords next to $ref; use anyOf for clarity.
        ref = schema.pop("$ref")
        schema["anyOf"] = [{"$ref": ref}, {"type": "null"}]
    else:
        schema["type"] = ["object", "null"]
    return schema


def _schema_from_field(field: dict[str, Any], *, openapi_version: str = "3.0.3") -> dict[str, Any]:
    if field.get("ref"):
        reference = _ref(str(field["ref"]))
        if field.get("nullable"):
            if openapi_version.startswith("3.1"):
                return {"anyOf": [reference, {"type": "null"}]}
            return {"allOf": [reference], "nullable": True}
        return reference

    type_name = field.get("type")
    if type_name == "array":
        items = field.get("items") or {"type": "object"}
        schema: dict[str, Any] = {"type": "array", "items": items if isinstance(items, dict) else {"type": "object"}}
    elif type_name in {"object", "string", "integer", "number", "boolean"}:
        schema = {"type": type_name}
    else:
        schema = _schema_from_python_text(str(type_name or ""))

    if field.get("format"):
        schema["format"] = field["format"]
    if field.get("description"):
        schema["description"] = field["description"]
    if field.get("enum"):
        schema["enum"] = field["enum"]
    if field.get("max_length"):
        schema["maxLength"] = field["max_length"]
    if field.get("nullable"):
        schema = _apply_nullable({**schema, "nullable": True}, openapi_version)
    if field.get("read_only"):
        schema["readOnly"] = True
    if field.get("write_only"):
        schema["writeOnly"] = True
    if field.get("default") is not None and isinstance(field["default"], (str, int, float, bool)):
        schema["default"] = field["default"]
    return schema


_DJANGO_FIELD_TYPES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (re.compile(r"EmailField|EmailStr", re.I), {"type": "string", "format": "email"}),
    (re.compile(r"URLField|AnyUrl|HttpUrl", re.I), {"type": "string", "format": "uri"}),
    (re.compile(r"DateTimeField", re.I), {"type": "string", "format": "date-time"}),
    (re.compile(r"DateField", re.I), {"type": "string", "format": "date"}),
    (re.compile(r"TimeField", re.I), {"type": "string", "format": "time"}),
    (re.compile(r"UUIDField|UUID", re.I), {"type": "string", "format": "uuid"}),
    (re.compile(r"DecimalField|FloatField|Decimal", re.I), {"type": "number"}),
    (re.compile(r"IntegerField|BigIntegerField|SmallIntegerField|PositiveIntegerField|AutoField", re.I), {"type": "integer"}),
    (re.compile(r"BooleanField|Boolean", re.I), {"type": "boolean"}),
    (re.compile(r"JSONField|DictField|HStoreField", re.I), {"type": "object"}),
    (re.compile(r"ManyToMany|ListField|ManyRelatedField", re.I), {"type": "array", "items": {"type": "object"}}),
    (re.compile(r"CharField|TextField|SlugField|FilePathField|ChoiceField|EmailField", re.I), {"type": "string"}),
)


def _schema_from_python_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        return {"type": "string"}

    serializer_match = re.search(r"\b([A-Za-z_][\w]*Serializer)\s*\(", cleaned)
    if serializer_match:
        return _ref(serializer_match.group(1))

    lowered = cleaned.lower()
    if lowered in {"str", "string"}:
        return {"type": "string"}
    if lowered in {"int", "integer"}:
        return {"type": "integer"}
    if lowered in {"float", "number", "decimal"}:
        return {"type": "number"}
    if lowered in {"bool", "boolean"}:
        return {"type": "boolean"}
    if lowered.startswith("list[") or lowered.startswith("list(") or "many=true" in lowered:
        return {"type": "array", "items": {"type": "object"}}
    if lowered.startswith("optional[") and cleaned.endswith("]"):
        return _schema_from_python_text(cleaned[len("Optional["): -1])

    for pattern, schema in _DJANGO_FIELD_TYPES:
        if pattern.search(cleaned):
            return dict(schema)

    return {"type": "string"}


def _component_schema(raw: dict[str, Any], *, openapi_version: str = "3.0.3") -> dict[str, Any]:
    kind = str(raw.get("type") or raw.get("kind") or "").lower()
    properties = raw.get("properties") or []
    assignments = raw.get("property_assignments") or {}

    if kind == "enum":
        values: list[Any] = []
        value_type: str | None = None
        mixed_types = False
        for item in properties:
            if isinstance(item, dict):
                value = item.get("value", item.get("name"))
                values.append(value)
                if value_type is None:
                    if isinstance(value, bool):
                        value_type = "boolean"
                    elif isinstance(value, int):
                        value_type = "integer"
                    elif isinstance(value, float):
                        value_type = "number"
                    elif isinstance(value, str):
                        value_type = "string"
                else:
                    # Check if subsequent values have different type
                    if value_type == "integer" and not isinstance(value, int):
                        mixed_types = True
                    elif value_type == "number" and not isinstance(value, (int, float)):
                        mixed_types = True
                    elif value_type == "boolean" and not isinstance(value, bool):
                        mixed_types = True
                    elif value_type == "string" and not isinstance(value, str):
                        mixed_types = True
            elif item:
                values.append(item)
        cleaned = [value for value in values if value is not None]
        if value_type is None or mixed_types:
            value_type = "string"
        return {"type": value_type, "enum": cleaned}

    props: dict[str, Any] = {}
    required: list[str] = []

    if isinstance(properties, dict):
        properties = [
            {"name": key, **value} if isinstance(value, dict) else {"name": key, "type": value}
            for key, value in properties.items()
        ]

    for item in properties if isinstance(properties, (list, tuple)) else []:
        if isinstance(item, str):
            props[item] = _schema_from_python_text(str(assignments.get(item) or ""))
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        props[str(name)] = _schema_from_field(item, openapi_version=openapi_version)
        if item.get("required"):
            required.append(str(name))

    schema: dict[str, Any] = {"type": "object"}
    if props:
        schema["properties"] = props
    if required:
        schema["required"] = required
    if raw.get("description"):
        schema["description"] = raw["description"]
    return schema


def build_openapi_from_analysis(
    record: AnalysisRecord,
    *,
    title: str | None = None,
    openapi_version: str = "3.0.3",
) -> dict[str, Any]:
    summary = record.summary
    project_name = Path(summary.project_path).name or "API"
    components_raw = record.components or {}

    schemas: dict[str, Any] = {}
    for name, raw in components_raw.items():
        if isinstance(raw, dict):
            schemas[str(name)] = _component_schema(raw, openapi_version=openapi_version)

    security_schemes: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    for endpoint in record.endpoints:
        method = str(endpoint.get("method", "")).lower()
        path = _endpoint_path(endpoint)
        if method not in _HTTP_METHODS or not path:
            continue
        if not path.startswith("/"):
            path = "/" + path

        view = endpoint.get("view") or "operation"
        function = endpoint.get("function")
        success_status = "200"
        if method == "post" and function in {"create"}:
            success_status = "201"
        elif method == "delete":
            success_status = "204"

        success_response: dict[str, Any] = {"description": "Successful response"}
        if success_status != "204":
            success_response["content"] = {"application/json": {"schema": {"type": "object"}}}

        operation: dict[str, Any] = {
            "operationId": f"{method}_{slugify(path.strip('/') or 'root').replace('-', '_')}",
            "tags": [path.strip("/").split("/")[0] or "default"],
            "responses": {success_status: success_response},
        }
        if function and function != view:
            operation["summary"] = f"{view}.{function}"
        else:
            operation["summary"] = str(view)
        if endpoint.get("description"):
            operation["description"] = str(endpoint["description"])

        parameters = _endpoint_parameters(endpoint)
        if parameters:
            operation["parameters"] = parameters

        security = endpoint.get("security")
        if isinstance(security, list) and security:
            operation["security"] = security
            for requirement in security:
                if not isinstance(requirement, dict):
                    continue
                for scheme_name, scheme in (endpoint.get("security_schemes") or {}).items():
                    if isinstance(scheme, dict):
                        security_schemes.setdefault(str(scheme_name), scheme)

        request_model = endpoint.get("request_serializer") or endpoint.get("request_model")
        if request_model and request_model in schemas:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _ref(str(request_model))}},
            }
        else:
            form_body = _form_request_body(endpoint)
            inline_body = _inline_json_body(endpoint)
            if form_body is not None:
                operation["requestBody"] = form_body
            elif inline_body is not None:
                operation["requestBody"] = inline_body

        response_model = endpoint.get("response_serializer") or endpoint.get("response_model")
        if response_model and response_model in schemas and success_status != "204":
            response_schema: dict[str, Any] = _ref(str(response_model))
            if endpoint.get("response_many"):
                response_schema = {"type": "array", "items": response_schema}
            operation["responses"][success_status]["content"] = {
                "application/json": {"schema": response_schema}
            }

        paths.setdefault(path, {})[method] = operation

    spec: dict[str, Any] = {
        "openapi": openapi_version,
        "info": {
            "title": title or f"{project_name} API",
            "version": "0.1.0",
            "description": (
                f"OpenAPI specification generated statically from {summary.framework} "
                f"sources in {project_name}."
            ),
        },
        "paths": paths,
    }
    if schemas or security_schemes:
        spec["components"] = {}
        if schemas:
            spec["components"]["schemas"] = schemas
        if security_schemes:
            spec["components"]["securitySchemes"] = security_schemes
    return spec


def write_spec(spec: dict[str, Any], output_dir: Path, *, fmt: str = "yml") -> Path:
    ensure_directory(output_dir)
    fmt = fmt.lower()
    if fmt not in {"yaml", "yml", "json"}:
        raise SpecGenerationError("fmt must be 'yml', 'yaml' or 'json'")
    if fmt == "json":
        import json

        target = output_dir / "openapi.json"
        atomic_write_text(target, json.dumps(spec, indent=2, ensure_ascii=False))
    else:
        suffix = ".yaml" if fmt == "yaml" else ".yml"
        target = output_dir / f"openapi{suffix}"
        atomic_write_text(target, yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    return target


# ------------------------------------------------------------------ LLM build
async def _generate_with_llm(
    project_path: Path,
    output_dir: Path,
    *,
    spec_model: str | None,
    context_model: str | None,
    use_dynamic: bool,
    django_settings_module: str | None,
    django_settings_file: str | None,
    django_urls_file: str | None,
    batch_size: int,
    max_concurrency: int,
    skip_components: bool,
    skip_missing_context: bool,
    force_add_components: bool,
    openapi_version: str,
    logger: Any,
) -> Path:
    ensure_knowl_on_path()

    try:
        from common.interfaces.batch_processor import BatchProcessor
        from common.interfaces.prompt_management import PromptManager
        from common.interfaces.spec_manager import OpenAPISpecManager
        from common.llm.llm import LLMManager
        from common.stats import StatsCollector
        from django_analyzer import DjangoAnalyzer
        from gen_apidocs2 import SpecGenerator
        from python_analyzer import PythonCodeAnalyzer
    except Exception as exc:  # pragma: no cover
        raise SpecGenerationError(
            "Could not import the knowl-apidocs generation pipeline.",
            hint="Ensure PYOPENAPI_KNOWL_ROOT points at a complete knowl-apidocs checkout.",
        ) from exc

    ensure_directory(output_dir)

    code_analyzer = PythonCodeAnalyzer()
    framework_analyzer = DjangoAnalyzer(
        code_analyzer=code_analyzer,
        project_path=str(project_path),
        logger=logger,
        use_dynamic=use_dynamic,
        settings_module_str=django_settings_module,
        explicit_settings_file_path=django_settings_file,
        explicit_urls_file_path=django_urls_file,
    )

    prompt_manager = PromptManager(framework_analyzer)
    batch_processor = BatchProcessor(
        default_batch_size=batch_size,
        max_concurrency=max_concurrency,
        request_spacing=2,
        adaptive_spacing=True,
        min_spacing=1,
        max_spacing=3,
    )
    spec_manager = OpenAPISpecManager(repo_name=project_path.name or "API")
    stats_collector = StatsCollector(
        repo_name=project_path.name or "API",
        output_dir=str(output_dir / "stats"),
        logger=logger,
    )
    llm_manager = LLMManager(max_retries=3, logger=logger, stats_collector=stats_collector)
    if not getattr(llm_manager, "providers", None):
        raise LLMNotConfiguredError(
            "No LLM provider is configured.",
            hint=(
                "Set AZURE_CONFIG_NAMES/AZURE_ENDPOINT_*/AZURE_API_KEY_* (and AZURE_MODEL_MAP_*), "
                "or GOOGLE_API_KEY, or DEEPSEEK_API_KEY. Use engine='static' to skip the LLM."
            ),
        )

    generator = SpecGenerator(
        code_analyzer=code_analyzer,
        framework_analyzer=framework_analyzer,
        prompt_manager=prompt_manager,
        llm_manager=llm_manager,
        spec_manager=spec_manager,
        batch_processor=batch_processor,
        stats_collector=stats_collector,
        spec_model_name=spec_model,
        context_model_name=context_model,
        logger=logger,
        skip_components=skip_components,
        skip_missing_context=skip_missing_context,
        force_add_components=force_add_components,
    )

    with redirect_stdout_to_stderr():
        spec_path = await generator.generate_spec(str(project_path), str(output_dir))
    spec_path = Path(spec_path)
    if spec_path.is_file() and openapi_version:
        try:
            generated = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            if isinstance(generated, dict) and generated.get("openapi") != openapi_version:
                generated["openapi"] = openapi_version
                atomic_write_text(
                    spec_path,
                    json.dumps(generated, indent=2, ensure_ascii=False)
                    if spec_path.suffix == ".json"
                    else yaml.safe_dump(generated, sort_keys=False, allow_unicode=True),
                )
        except (yaml.YAMLError, OSError):  # pragma: no cover - defensive
            pass
    return spec_path


# --------------------------------------------------------------------- public
async def generate_openapi(
    record: AnalysisRecord,
    output_dir: str | Path,
    *,
    engine: str = "auto",
    fmt: str = "yml",
    spec_model: str | None = None,
    context_model: str | None = None,
    use_dynamic: bool = False,
    django_settings_module: str | None = None,
    django_settings_file: str | None = None,
    django_urls_file: str | None = None,
    batch_size: int = 30,
    max_concurrency: int = 5,
    skip_components: bool = False,
    skip_missing_context: bool = False,
    force_add_components: bool = False,
    openapi_version: str | None = None,
    progress_path: str | Path | None = None,
    allow_partial: bool = False,
    logger: Any | None = None,
) -> GenerationResult:
    logger = logger or get_logger("pyopenapi.spec")
    stopwatch = Stopwatch()
    warnings: list[str] = []
    out = Path(output_dir).expanduser().resolve()
    ensure_directory(out)
    target_version = openapi_version or get_settings().openapi_version
    llm_usage = None

    resolved_engine = engine.lower()
    allowed = {"auto", "agent", "llm", "static", *_PROVIDER_ENGINES}
    if resolved_engine not in allowed:
        raise SpecGenerationError(
            "engine must be one of: auto, agent, static, llm, nvidia, openrouter, "
            "lmstudio, llamacpp, ollama, openai-compatible"
        )

    framework = record.summary.framework
    if resolved_engine == "auto":
        from .agent_llm import agent_llm_available

        provider_available = agent_llm_available(settings=get_settings())
        if framework == "django" and legacy_llm_available() and not provider_available:
            resolved_engine = "llm"
        elif provider_available:
            resolved_engine = "agent"
        else:
            resolved_engine = "static"
            warnings.append(
                "No LLM provider was configured; generated a deterministic static spec. "
                "Configure NVIDIA_API_KEY/NVIDIA_NIM_API_KEY or OPENROUTER_API_KEY "
                "(or pass engine='agent') for agent-enriched output."
            )

    if resolved_engine == "nvidia" and not nvidia_available():
        raise LLMNotConfiguredError(
            "NVIDIA_API_KEY is not configured.",
            hint="Set NVIDIA_API_KEY (or PYOPENAPI_NVIDIA_API_KEY) in the environment or .env file.",
        )

    if resolved_engine == "agent":
        from .pipeline import PipelineOrchestrator

        pipeline = PipelineOrchestrator(
            settings=get_settings(),
            model=spec_model,
            max_concurrency=max_concurrency or 5,
        )
        pipeline_result = await pipeline.run(
            record,
            out,
            fmt=fmt,
            openapi_version=target_version,
            progress_path=progress_path,
            allow_partial=allow_partial,
        )
        spec = pipeline_result.spec
        spec_path = pipeline_result.spec_path
        llm_usage = pipeline_result.usage
        warnings.extend(pipeline_result.warnings)
    elif resolved_engine == "llm":
        if framework != "django":
            raise FrameworkNotSupportedError(
                f"LLM spec generation currently supports Django only (detected: {framework}).",
                hint="Use engine='static' for Flask/FastAPI, or analyze the project as Django.",
            )
        spec_path = await _generate_with_llm(
            Path(record.summary.project_path),
            out,
            spec_model=spec_model,
            context_model=context_model,
            use_dynamic=use_dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
            batch_size=batch_size,
            max_concurrency=max_concurrency,
            skip_components=skip_components,
            skip_missing_context=skip_missing_context,
            force_add_components=force_add_components,
            openapi_version=target_version,
            logger=logger,
        )
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    else:
        spec = build_openapi_from_analysis(record, openapi_version=target_version)
        if resolved_engine in _PROVIDER_ENGINES:
            llm_usage = LLMUsage()
            spec, provider_warnings = await enrich_openapi_with_llm(
                spec,
                settings=get_settings(),
                provider_name=resolved_engine,
                spec_model=spec_model,
                context_model=context_model,
                usage=llm_usage,
            )
            spec["openapi"] = target_version
            warnings.extend(provider_warnings)
        spec_path = write_spec(spec, out, fmt=fmt)

    info = spec.get("info") or {}
    paths = spec.get("paths") or {}
    components = (spec.get("components") or {}).get("schemas") or {}

    return GenerationResult(
        analysis_id=record.summary.analysis_id,
        project_path=record.summary.project_path,
        framework=framework,
        spec_path=str(spec_path),
        format="json" if str(spec_path).endswith(".json") else "yaml",
        endpoint_count=sum(
            1
            for path_item in paths.values()
            if isinstance(path_item, dict)
            for method in path_item
            if method.lower() in _HTTP_METHODS
        ),
        component_count=len(components) if isinstance(components, dict) else 0,
        duration_ms=stopwatch.elapsed_ms,
        warnings=[*record.summary.warnings, *warnings],
        openapi_version=spec.get("openapi"),
        title=info.get("title"),
        llm_usage=llm_usage,
    )


def generate_openapi_sync(record: AnalysisRecord, output_dir: str | Path, **kwargs: Any) -> GenerationResult:
    return asyncio.run(generate_openapi(record, output_dir, **kwargs))


# --------------------------------------------------------------- merge / diff
def _load_specs(specs: list[str | Path | dict[str, Any]]) -> list[dict[str, Any]]:
    from .validation import load_spec

    loaded: list[dict[str, Any]] = []
    for item in specs:
        document = load_spec(item)
        loaded.append(document)
    return loaded


def merge_specs(
    specs: list[str | Path | dict[str, Any]],
    *,
    title: str | None = None,
    version: str = "0.1.0",
    openapi_version: str | None = None,
    on_conflict: str = "warn",
) -> dict[str, Any]:
    """Merge multiple OpenAPI documents into one.

    Paths are unioned; a duplicate ``<method> <path>`` from a later document is
    skipped and reported. ``on_conflict`` controls duplicate handling:
    ``"first"`` keeps the first silently, ``"warn"`` keeps the first and records
    a warning, and ``"error"`` raises. Warnings are attached under the
    ``x-pyopenapi-warnings`` extension.
    """

    if on_conflict not in {"first", "warn", "error"}:
        raise SpecGenerationError("on_conflict must be one of: first, warn, error.")

    documents = _load_specs(specs)
    if not documents:
        raise SpecGenerationError("No OpenAPI documents were supplied to merge.")

    warnings: list[str] = []
    target_version = openapi_version or documents[0].get("openapi") or get_settings().openapi_version
    merged: dict[str, Any] = {
        "openapi": target_version,
        "info": {
            "title": title or "Merged API",
            "version": version,
            "description": f"Merged specification combining {len(documents)} document(s).",
        },
        "paths": {},
        "components": {"schemas": {}, "securitySchemes": {}},
        "tags": [],
    }
    seen_tags: dict[str, dict[str, Any]] = {}

    for index, document in enumerate(documents):
        label = str((document.get("info") or {}).get("title") or f"document[{index}]")
        paths = document.get("paths") or {}
        if isinstance(paths, dict):
            for path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                bucket = merged["paths"].setdefault(path, {})
                for method, operation in path_item.items():
                    if method.lower() not in _HTTP_METHODS:
                        if method == "parameters" and "parameters" not in bucket:
                            bucket["parameters"] = operation
                        continue
                    if method.lower() in bucket:
                        message = (
                            f"Duplicate operation '{method.upper()} {path}' from {label} was skipped."
                        )
                        if on_conflict == "error":
                            raise SpecGenerationError(message)
                        if on_conflict == "warn":
                            warnings.append(message)
                        continue
                    bucket[method] = copy.deepcopy(operation)

        components = document.get("components") or {}
        for key in ("schemas", "securitySchemes"):
            source = components.get(key) if isinstance(components, dict) else None
            if not isinstance(source, dict):
                continue
            target = merged["components"].setdefault(key, {})
            for name, value in source.items():
                if name in target:
                    message = f"Duplicate {key} entry '{name}' from {label} was skipped."
                    if on_conflict == "error":
                        raise SpecGenerationError(message)
                    if on_conflict == "warn":
                        warnings.append(message)
                    continue
                target[name] = copy.deepcopy(value)

        tags = document.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict) and tag.get("name"):
                    seen_tags.setdefault(str(tag["name"]), tag)

    if seen_tags:
        merged["tags"] = list(seen_tags.values())
    if not merged["components"]["securitySchemes"]:
        merged["components"].pop("securitySchemes")
    if not merged["components"]["schemas"]:
        merged["components"].pop("schemas")
    if not merged["components"]:
        merged.pop("components")
    if warnings:
        merged["x-pyopenapi-warnings"] = warnings
    return merged


def _operation_signature(operation: Any) -> str:
    return json.dumps(operation, sort_keys=True, ensure_ascii=False)


def _schema_change_breaking(old_schema: Any, new_schema: Any) -> bool:
    """Heuristic: removals and type changes break clients; additions do not."""

    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        return True
    old_props = old_schema.get("properties") or {}
    new_props = new_schema.get("properties") or {}
    if isinstance(old_props, dict) and isinstance(new_props, dict):
        if set(old_props) - set(new_props):
            return True
        for name in set(old_props) & set(new_props):
            old_type = (old_props.get(name) or {}).get("type")
            new_type = (new_props.get(name) or {}).get("type")
            if old_type and new_type and old_type != new_type:
                return True
    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])
    return bool(new_required - old_required)


def diff_specs(
    old: str | Path | dict[str, Any],
    new: str | Path | dict[str, Any],
) -> DiffResult:
    """Compare two OpenAPI documents and classify changes.

    Removed endpoints/components and request/response schema changes are marked
    as breaking (``severity="error"``); additive changes are ``info``.
    """

    old_doc = _load_specs([old])[0]
    new_doc = _load_specs([new])[0]
    changes: list[DiffChange] = []

    def operations(document: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        paths = document.get("paths") or {}
        if isinstance(paths, dict):
            for path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                for method, operation in path_item.items():
                    if method.lower() in _HTTP_METHODS:
                        result[f"{method.upper()} {path}"] = operation
        return result

    old_ops = operations(old_doc)
    new_ops = operations(new_doc)

    for key in sorted(set(new_ops) - set(old_ops)):
        changes.append(DiffChange(kind="added", path=key, message="Endpoint added.", severity="info"))
    for key in sorted(set(old_ops) - set(new_ops)):
        changes.append(
            DiffChange(kind="removed", path=key, message="Endpoint removed.", severity="error")
        )
    for key in sorted(set(old_ops) & set(new_ops)):
        if _operation_signature(old_ops[key]) != _operation_signature(new_ops[key]):
            old_op, new_op = old_ops[key], new_ops[key]
            breaking = _request_response_changed(old_op, new_op)
            changes.append(
                DiffChange(
                    kind="changed",
                    path=key,
                    message="Endpoint definition changed.",
                    severity="error" if breaking else "warning",
                )
            )

    def schemas(document: dict[str, Any]) -> dict[str, Any]:
        components = document.get("components") or {}
        value = components.get("schemas") if isinstance(components, dict) else None
        return value if isinstance(value, dict) else {}

    old_schemas = schemas(old_doc)
    new_schemas = schemas(new_doc)
    for name in sorted(set(new_schemas) - set(old_schemas)):
        changes.append(
            DiffChange(kind="added", path=f"#/components/schemas/{name}", message="Schema added.", severity="info")
        )
    for name in sorted(set(old_schemas) - set(new_schemas)):
        changes.append(
            DiffChange(
                kind="removed",
                path=f"#/components/schemas/{name}",
                message="Schema removed.",
                severity="error",
            )
        )
    for name in sorted(set(old_schemas) & set(new_schemas)):
        if _operation_signature(old_schemas[name]) != _operation_signature(new_schemas[name]):
            breaking = _schema_change_breaking(old_schemas[name], new_schemas[name])
            changes.append(
                DiffChange(
                    kind="changed",
                    path=f"#/components/schemas/{name}",
                    message="Schema changed.",
                    severity="error" if breaking else "warning",
                )
            )

    breaking = any(change.severity == "error" for change in changes)
    changed = bool(changes)
    summary = (
        "No changes detected."
        if not changed
        else f"{len(changes)} change(s) detected"
        + (" including breaking changes." if breaking else ".")
    )
    return DiffResult(
        changed=changed,
        breaking=breaking,
        change_count=len(changes),
        changes=changes,
        summary=summary,
    )


def _request_response_changed(old_op: Any, new_op: Any) -> bool:
    def shape(operation: Any) -> tuple[Any, Any]:
        if not isinstance(operation, dict):
            return (None, None)
        request = (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        responses = operation.get("responses") or {}
        response_schema = None
        if isinstance(responses, dict):
            for response in responses.values():
                schema = (
                    (response or {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if isinstance(schema, dict):
                    response_schema = schema
                    break
        return (request, response_schema)

    return shape(old_op) != shape(new_op)
