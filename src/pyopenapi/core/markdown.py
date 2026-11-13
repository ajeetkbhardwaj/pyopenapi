"""Deterministic OpenAPI 3.0 -> Markdown generator.

No LLM is involved: the output is reproducible and safe to run in CI. The
generator supports a multi-file layout (index + one file per endpoint and
component) and a single-file layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import MarkdownGenerationError
from ..models import MarkdownFile, MarkdownResult
from ..utils import atomic_write_text, ensure_directory, slugify
from .validation import load_spec

_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _ref_name(ref: str) -> str:
    return ref.split("/")[-1]


class _Renderer:
    def __init__(self, spec: dict[str, Any], *, component_prefix: str = "") -> None:
        self.spec = spec
        self.components: dict[str, Any] = (spec.get("components") or {}).get("schemas") or {}
        self.component_prefix = component_prefix

    # ------------------------------------------------------------- primitives
    def link_component(self, name: str) -> str:
        if self.component_prefix:
            return f"[{name}]({self.component_prefix}{slugify(name)}.md)"
        return f"`{name}`"

    def type_label(self, schema: Any) -> str:
        if not isinstance(schema, dict):
            return "`any`"
        if "$ref" in schema:
            name = _ref_name(str(schema["$ref"]))
            return self.link_component(name)
        if "allOf" in schema:
            return " & ".join(self.type_label(item) for item in schema["allOf"])
        if "oneOf" in schema:
            return " \\| ".join(self.type_label(item) for item in schema["oneOf"])
        if "anyOf" in schema:
            return " \\| ".join(self.type_label(item) for item in schema["anyOf"])
        schema_type = schema.get("type")
        if schema_type == "array":
            return f"array<{self.type_label(schema.get('items', {}))}>"
        if schema_type:
            fmt = schema.get("format")
            label = f"{schema_type}({fmt})" if fmt else str(schema_type)
            if schema.get("nullable"):
                label += " \\| null"
            return f"`{label}`"
        if schema.get("properties"):
            return "`object`"
        return "`any`"

    def schema_table(self, schema: Any, *, level: int = 0) -> list[str]:
        if not isinstance(schema, dict) or level > 6:
            return []

        if "$ref" in schema:
            name = _ref_name(str(schema["$ref"]))
            return [f"See {self.link_component(name)}."]

        for combinator in ("allOf", "oneOf", "anyOf"):
            if combinator in schema:
                lines = []
                for index, item in enumerate(schema[combinator], start=1):
                    lines.append(f"**{combinator} option {index}:** {self.type_label(item)}")
                    lines.extend(self.schema_table(item, level=level + 1))
                return lines

        if schema.get("type") == "array":
            return self.schema_table(schema.get("items", {}), level=level + 1)

        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            if schema.get("enum"):
                return ["**Allowed values:** " + ", ".join(f"`{value}`" for value in schema["enum"])]
            return []

        required = set(schema.get("required") or [])
        lines = ["| Field | Type | Required | Description |", "| --- | --- | --- | --- |"]
        for name, prop in properties.items():
            prop = prop if isinstance(prop, dict) else {}
            description = prop.get("description") or ""
            if prop.get("enum"):
                description = (description + " " if description else "") + "Values: " + ", ".join(
                    f"`{value}`" for value in prop["enum"]
                )
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{_escape(name)}`",
                        self.type_label(prop),
                        "yes" if name in required else "no",
                        _escape(description) or "—",
                    ]
                )
                + " |"
            )
        return lines

    def example_block(self, example: Any) -> list[str]:
        if example is None:
            return []
        try:
            text = json.dumps(example, indent=2, ensure_ascii=False)
        except TypeError:
            text = str(example)
        return ["```json", text, "```"]

    # -------------------------------------------------------------- operation
    def render_operation(self, method: str, path: str, operation: dict[str, Any]) -> str:
        title = operation.get("summary") or f"{method.upper()} {path}"
        lines = [f"# {title}", "", f"`{method.upper()} {path}`", ""]

        if operation.get("description"):
            lines += [str(operation["description"]).strip(), ""]

        meta: list[str] = []
        if operation.get("operationId"):
            meta.append(f"**Operation ID:** `{operation['operationId']}`")
        if operation.get("tags"):
            meta.append("**Tags:** " + ", ".join(f"`{tag}`" for tag in operation["tags"]))
        if operation.get("deprecated"):
            meta.append("**Deprecated:** yes")
        if meta:
            lines += [*meta, ""]

        lines += self._render_parameters(operation)
        lines += self._render_request_body(operation)
        lines += self._render_responses(operation)
        lines += self._render_security(operation)

        return "\n".join(lines).rstrip() + "\n"

    def _render_parameters(self, operation: dict[str, Any]) -> list[str]:
        parameters = operation.get("parameters")
        if not isinstance(parameters, list) or not parameters:
            return []
        lines = ["## Parameters", "", "| Name | In | Type | Required | Description |", "| --- | --- | --- | --- | --- |"]
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            schema = parameter.get("schema") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{_escape(parameter.get('name', ''))}`",
                        _escape(parameter.get("in", "")),
                        self.type_label(schema),
                        "yes" if parameter.get("required") else "no",
                        _escape(parameter.get("description", "")) or "—",
                    ]
                )
                + " |"
            )
        lines.append("")
        return lines

    def _render_request_body(self, operation: dict[str, Any]) -> list[str]:
        body = operation.get("requestBody")
        if not isinstance(body, dict):
            return []
        lines = ["## Request Body", ""]
        if body.get("description"):
            lines += [str(body["description"]).strip(), ""]
        content = body.get("content") or {}
        for media_type, media in content.items():
            lines.append(f"**Content type:** `{media_type}`")
            lines.append("")
            schema = (media or {}).get("schema")
            lines += self.schema_table(schema)
            example = (media or {}).get("example")
            if example is not None:
                lines += ["", "**Example:**", ""]
                lines += self.example_block(example)
            lines.append("")
        return lines

    def _render_responses(self, operation: dict[str, Any]) -> list[str]:
        responses = operation.get("responses")
        if not isinstance(responses, dict) or not responses:
            return []
        lines = ["## Responses", ""]
        for status, response in responses.items():
            response = response if isinstance(response, dict) else {}
            description = response.get("description") or ""
            lines.append(f"### `{status}` {description}".rstrip())
            lines.append("")
            content = response.get("content") or {}
            for media_type, media in content.items():
                schema = (media or {}).get("schema")
                if schema:
                    lines.append(f"**Content type:** `{media_type}`")
                    lines.append("")
                    lines += self.schema_table(schema)
                    lines.append("")
                example = (media or {}).get("example")
                if example is not None:
                    lines += ["**Example:**", ""]
                    lines += self.example_block(example)
                    lines.append("")
        return lines

    def _render_security(self, operation: dict[str, Any]) -> list[str]:
        security = operation.get("security") or self.spec.get("security")
        if not security:
            return []
        lines = ["## Security", ""]
        for requirement in security:
            if isinstance(requirement, dict):
                for scheme, scopes in requirement.items():
                    scope_text = ", ".join(scopes) if scopes else "—"
                    lines.append(f"- `{scheme}` (scopes: {scope_text})")
            else:
                lines.append(f"- `{requirement}`")
        lines.append("")
        return lines

    # -------------------------------------------------------------- component
    def render_component(self, name: str, schema: dict[str, Any]) -> str:
        lines = [f"# {name}", ""]
        if schema.get("description"):
            lines += [str(schema["description"]).strip(), ""]
        if schema.get("title") and schema.get("title") != name:
            lines += [f"**Title:** {schema['title']}", ""]

        schema_type = schema.get("type") or ("object" if schema.get("properties") else None)
        if schema_type:
            lines += [f"**Type:** `{schema_type}`", ""]

        if schema.get("enum"):
            lines += ["## Allowed Values", ""]
            lines += [f"- `{value}`" for value in schema["enum"]]
            lines.append("")

        table = self.schema_table(schema)
        if table:
            lines += ["## Properties", "", *table, ""]
        else:
            lines += ["_No properties declared._", ""]

        return "\n".join(lines).rstrip() + "\n"


def _index_markdown(
    spec: dict[str, Any],
    endpoint_entries: list[tuple[str, str, str]],
    component_names: list[str],
) -> str:
    info = spec.get("info") or {}
    title = info.get("title") or "API Reference"
    lines = [f"# {title}", ""]
    if info.get("version"):
        lines.append(f"**Version:** {info['version']}")
    if info.get("description"):
        lines += ["", str(info["description"]).strip()]

    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        lines += ["", "## Servers", ""]
        for server in servers:
            if isinstance(server, dict):
                lines.append(f"- `{server.get('url', '')}` {server.get('description', '')}".rstrip())

    lines += ["", "## Endpoints", "", "| Method | Path | Summary |", "| --- | --- | --- |"]
    for method, path, filename in endpoint_entries:
        lines.append(f"| `{method.upper()}` | `{path}` | [{filename}](endpoints/{filename}) |")

    if component_names:
        lines += ["", "## Components", "", "| Name |", "| --- |"]
        for name in component_names:
            lines.append(f"| [{name}](components/{slugify(name)}.md) |")

    lines.append("")
    return "\n".join(lines)


def generate_markdown(
    source: str | Path | dict[str, Any],
    output_dir: str | Path,
    *,
    layout: str = "multi",
    title: str | None = None,
) -> MarkdownResult:
    if layout not in {"multi", "single"}:
        raise MarkdownGenerationError("layout must be 'multi' or 'single'")

    spec = load_spec(source)
    if title:
        spec.setdefault("info", {})
        spec["info"]["title"] = title

    out = Path(output_dir).expanduser().resolve()
    ensure_directory(out)
    files: list[MarkdownFile] = []

    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        raise MarkdownGenerationError("'paths' must be an object")

    if layout == "single":
        renderer = _Renderer(spec)
        info = spec.get("info") or {}
        chunks = [f"# {info.get('title') or 'API Reference'}", ""]
        if info.get("version"):
            chunks.append(f"**Version:** {info['version']}")
        if info.get("description"):
            chunks += ["", str(info["description"]).strip()]
        chunks.append("")
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                chunks += ["---", "", renderer.render_operation(method.lower(), str(path), operation)]
        for name, schema in (renderer.components or {}).items():
            if isinstance(schema, dict):
                chunks += ["---", "", renderer.render_component(str(name), schema)]
        target = out / "api.md"
        content = "\n".join(chunks)
        atomic_write_text(target, content)
        files.append(MarkdownFile(path=str(target), title="API Reference", bytes=len(content.encode("utf-8"))))
        return MarkdownResult(
            output_dir=str(out),
            layout="single",
            file_count=1,
            files=files,
            index_path=str(target),
        )

    endpoints_dir = ensure_directory(out / "endpoints")
    components_dir = ensure_directory(out / "components")

    endpoint_entries: list[tuple[str, str, str]] = []
    used_names: set[str] = set()

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            base = f"{method.lower()}-{slugify(str(path).strip('/') or 'root')}"
            filename = f"{base}.md"
            counter = 2
            while filename in used_names:
                filename = f"{base}-{counter}.md"
                counter += 1
            used_names.add(filename)

            renderer = _Renderer(spec, component_prefix="../components/")
            content = renderer.render_operation(method.lower(), str(path), operation)
            target = endpoints_dir / filename
            atomic_write_text(target, content)
            files.append(
                MarkdownFile(
                    path=str(target),
                    title=operation.get("summary") or f"{method.upper()} {path}",
                    bytes=len(content.encode("utf-8")),
                )
            )
            endpoint_entries.append((method, str(path), filename))

    component_names: list[str] = []
    renderer = _Renderer(spec)
    for name, schema in (renderer.components or {}).items():
        if not isinstance(schema, dict):
            continue
        component_names.append(str(name))
        content = renderer.render_component(str(name), schema)
        target = components_dir / f"{slugify(str(name))}.md"
        atomic_write_text(target, content)
        files.append(
            MarkdownFile(path=str(target), title=str(name), bytes=len(content.encode("utf-8")))
        )

    index_content = _index_markdown(spec, endpoint_entries, component_names)
    index_path = out / "index.md"
    atomic_write_text(index_path, index_content)
    files.append(
        MarkdownFile(path=str(index_path), title="Index", bytes=len(index_content.encode("utf-8")))
    )

    return MarkdownResult(
        output_dir=str(out),
        layout="multi",
        file_count=len(files),
        files=files,
        index_path=str(index_path),
    )
