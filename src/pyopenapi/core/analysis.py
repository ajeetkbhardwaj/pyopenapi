"""Analysis orchestration, caching and normalisation."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..analyzers import AnalysisOptions, analyze as run_analyzer
from ..config import Settings, get_settings
from ..detection import detect_framework
from ..errors import (
    AnalysisError,
    AnalysisNotFoundError,
    CacheError,
    FrameworkDetectionError,
    FrameworkNotSupportedError,
)
from ..logging_utils import get_logger
from ..models import (
    AnalysisSummary,
    CacheEntry,
    CacheStats,
    ComponentInfo,
    EndpointInfo,
    EndpointParameter,
    FieldInfo,
)
from ..source import resolve_project_source
from ..utils import (
    Stopwatch,
    atomic_write_json,
    dir_size,
    ensure_directory,
    file_lock,
    jsonable,
    read_json,
    short_hash,
    utc_now,
    utc_now_iso,
)

_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")
_SUPPORTED = {"django", "flask", "fastapi"}


@dataclass
class AnalysisRecord:
    summary: AnalysisSummary
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AnalysisManager:
    # In-process dedupe so concurrent calls for the same analysis share one run.
    _inflight_lock = threading.Lock()
    _inflight: dict[str, threading.Lock] = {}

    def __init__(self, settings: Settings | None = None, logger: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.logger = logger or get_logger("pyopenapi.analysis")
        self.base_dir = self.settings.resolved_cache_dir() / "analyses"
        ensure_directory(self.base_dir)

    # ------------------------------------------------------------------ paths
    def analysis_dir(self, analysis_id: str) -> Path:
        if not _ID_RE.match(analysis_id):
            raise AnalysisNotFoundError(
                f"Invalid analysis id: {analysis_id!r}",
                hint="Analysis ids are 16-char hex strings returned by analyze_project.",
                details={"analysis_id": analysis_id},
            )
        return self.base_dir / analysis_id

    def _paths(self, analysis_id: str) -> dict[str, Path]:
        root = self.analysis_dir(analysis_id)
        return {
            "root": root,
            "summary": root / "analysis.json",
            "endpoints": root / "endpoints.json",
            "components": root / "components.json",
            "metadata": root / "metadata.json",
            "work": root / "work",
            "lock": root / ".lock",
        }

    @classmethod
    def _thread_lock_for(cls, analysis_id: str) -> threading.Lock:
        with cls._inflight_lock:
            return cls._inflight.setdefault(analysis_id, threading.Lock())

    # --------------------------------------------------------------- analysis
    def analyze(
        self,
        project_path: str | Path,
        framework: str = "auto",
        *,
        use_dynamic: bool | None = None,
        django_settings_module: str | None = None,
        django_settings_file: str | None = None,
        django_urls_file: str | None = None,
        include_endpoints: bool = True,
        include_components: bool = True,
        force: bool = False,
        refresh_repo: bool = False,
        max_files: int | None = None,
        max_file_bytes: int | None = None,
    ) -> AnalysisRecord:
        resolved = resolve_project_source(project_path, self.settings, refresh=refresh_repo)
        if not resolved.exists():
            raise AnalysisError(
                f"Project path does not exist: {resolved}",
                hint="Pass a local directory or a GitHub URL/shorthand (owner/repo).",
                details={"project_path": str(resolved)},
            )
        if not resolved.is_dir():
            raise AnalysisError(
                f"Project path is not a directory: {resolved}",
                hint="Point analyze_project at the repository/project root directory.",
                details={"project_path": str(resolved)},
            )

        scan_files = self._clamp_scan_files(max_files)
        scan_bytes = self._clamp_file_bytes(max_file_bytes)

        detection = None
        requested = (framework or "auto").lower()
        if requested in {"auto", "detect", ""}:
            detection = detect_framework(
                resolved,
                max_files=scan_files,
                max_file_bytes=scan_bytes,
            )
            if detection.framework == "unknown" or not detection.supported:
                raise FrameworkDetectionError(
                    "Could not confidently detect a supported Python web framework.",
                    hint=(
                        "Pass framework='django'|'flask'|'fastapi' explicitly. "
                        f"Evidence: {detection.evidence or detection.reason}"
                    ),
                    details={"scores": detection.scores, "reason": detection.reason},
                )
            framework = detection.framework
        else:
            framework = requested

        if framework not in _SUPPORTED:
            raise FrameworkNotSupportedError(
                f"Unsupported framework: {framework}",
                hint="Supported frameworks are: django, flask, fastapi.",
                details={"framework": framework},
            )

        dynamic = self.settings.default_use_dynamic if use_dynamic is None else use_dynamic
        options = AnalysisOptions(
            framework=framework,
            use_dynamic=dynamic,
            django_settings_module=django_settings_module,
            django_settings_file=django_settings_file,
            django_urls_file=django_urls_file,
            include_endpoints=include_endpoints,
            include_components=include_components,
            max_files=scan_files,
            max_file_bytes=scan_bytes,
        )

        analysis_id = short_hash(
            resolved,
            framework,
            dynamic,
            django_settings_module,
            django_settings_file,
            django_urls_file,
            include_endpoints,
            include_components,
            scan_files,
            scan_bytes,
        )
        paths = self._paths(analysis_id)

        if not force and paths["summary"].is_file():
            return self.load(analysis_id)

        thread_lock = self._thread_lock_for(analysis_id)
        acquired = thread_lock.acquire(timeout=self.settings.lock_timeout_seconds)
        if not acquired:
            raise AnalysisError(
                "Another analysis for this project is already running in this process.",
                hint="Wait for the in-flight analysis to finish, or pass force=True to queue behind it.",
                details={"analysis_id": analysis_id},
            )
        try:
            if not force and paths["summary"].is_file():
                return self.load(analysis_id)
            return self._run_analysis(
                analysis_id,
                resolved,
                framework,
                dynamic,
                options,
                paths,
                detection,
                django_settings_module,
                django_settings_file,
                django_urls_file,
                include_endpoints,
                include_components,
            )
        finally:
            thread_lock.release()

    def _run_analysis(
        self,
        analysis_id: str,
        resolved: Path,
        framework: str,
        dynamic: bool,
        options: AnalysisOptions,
        paths: dict[str, Path],
        detection: Any,
        django_settings_module: str | None,
        django_settings_file: str | None,
        django_urls_file: str | None,
        include_endpoints: bool,
        include_components: bool,
    ) -> AnalysisRecord:
        try:
            with file_lock(paths["lock"], timeout=self.settings.lock_timeout_seconds):
                if paths["summary"].is_file():
                    return self.load(analysis_id)

                ensure_directory(paths["work"])
                stopwatch = Stopwatch()
                self.logger.info("Analyzing %s as %s (id=%s)", resolved, framework, analysis_id)

                raw = run_analyzer(resolved, paths["work"], options, self.logger)

                endpoints = [jsonable(item) for item in raw.endpoints]
                components = {
                    str(name): jsonable(payload) for name, payload in raw.components.items()
                }
                warnings = list(dict.fromkeys([*raw.warnings]))
                if detection is not None:
                    if detection.confidence < 0.5:
                        warnings.append(
                            f"Framework auto-detection confidence is low ({detection.confidence:.2f}); "
                            f"detected '{detection.framework}'. Pass an explicit framework to override."
                        )
                    for item in detection.evidence[:3]:
                        warnings.append(f"Detection evidence: {item}")

                endpoint_source = raw.metadata.get("endpoint_source")
                dynamic_requested = bool(raw.metadata.get("dynamic_requested", dynamic))
                used_dynamic = endpoint_source == "dynamic"
                degraded = bool(raw.metadata.get("degraded", False)) or (
                    dynamic_requested and not used_dynamic
                )
                if dynamic_requested and not used_dynamic:
                    warnings.append(
                        "Dynamic Django extraction was requested but static analysis was used. "
                        "Set PYOPENAPI_DEFAULT_USE_DYNAMIC/use_dynamic only when the project's "
                        "dependencies are importable by the server interpreter."
                    )

                metadata = {
                    "analysis_file": raw.metadata.get("analysis_file"),
                    "endpoint_source": endpoint_source,
                    "component_source": raw.metadata.get("component_source"),
                    "dynamic_requested": dynamic_requested,
                    "dynamic_used": used_dynamic,
                    "degraded": degraded,
                    "default_settings": raw.metadata.get("default_settings", {}),
                    "detection": detection.model_dump() if detection else None,
                }

                summary = AnalysisSummary(
                    analysis_id=analysis_id,
                    project_path=str(resolved),
                    framework=framework,
                    endpoint_count=len(endpoints),
                    component_count=len(components),
                    created_at=utc_now_iso(),
                    duration_ms=stopwatch.elapsed_ms,
                    cache_dir=str(paths["root"]),
                    output_dir=str(paths["work"]),
                    warnings=warnings,
                    used_dynamic=used_dynamic,
                    degraded=degraded,
                    options={
                        "use_dynamic": dynamic,
                        "dynamic_requested": dynamic_requested,
                        "dynamic_used": used_dynamic,
                        "degraded": degraded,
                        "include_endpoints": include_endpoints,
                        "include_components": include_components,
                        "django_settings_module": django_settings_module,
                        "django_settings_file": django_settings_file,
                        "django_urls_file": django_urls_file,
                        "endpoint_source": endpoint_source,
                        "component_source": raw.metadata.get("component_source"),
                        "max_files": options.max_files,
                        "max_file_bytes": options.max_file_bytes,
                    },
                )

                atomic_write_json(paths["summary"], summary.model_dump())
                atomic_write_json(paths["endpoints"], endpoints)
                atomic_write_json(paths["components"], components)
                atomic_write_json(paths["metadata"], metadata)

                self.logger.info(
                    "Analysis complete: %s endpoints, %s components in %sms",
                    len(endpoints),
                    len(components),
                    summary.duration_ms,
                )

        except TimeoutError as exc:
            raise AnalysisError(
                "Timed out waiting for the analysis lock.",
                hint=(
                    "Another analysis process holds the lock. Wait for it to finish, delete the "
                    "stale .lock file under the analysis cache directory, or raise "
                    "PYOPENAPI_LOCK_TIMEOUT_SECONDS."
                ),
                details={"analysis_id": analysis_id},
            ) from exc

        return AnalysisRecord(summary=summary, endpoints=endpoints, components=components, metadata=metadata)

    def _clamp_scan_files(self, value: int | None) -> int:
        ceiling = 1_000_000
        if value is None:
            return self.settings.max_scan_files
        return max(1, min(int(value), ceiling))

    def _clamp_file_bytes(self, value: int | None) -> int:
        ceiling = 100_000_000
        if value is None:
            return self.settings.max_file_bytes
        return max(1, min(int(value), ceiling))

    # ----------------------------------------------------------------- loading
    def load(self, analysis_id: str) -> AnalysisRecord:
        paths = self._paths(analysis_id)
        if not paths["summary"].is_file():
            raise AnalysisNotFoundError(
                f"Analysis '{analysis_id}' was not found.",
                hint="Run analyze_project first, or call list_analyses to inspect cached ids.",
                details={"analysis_id": analysis_id},
            )
        summary = AnalysisSummary.model_validate(read_json(paths["summary"]))
        endpoints = read_json(paths["endpoints"]) if paths["endpoints"].is_file() else []
        components = read_json(paths["components"]) if paths["components"].is_file() else {}
        metadata = read_json(paths["metadata"]) if paths["metadata"].is_file() else {}
        return AnalysisRecord(summary=summary, endpoints=endpoints, components=components, metadata=metadata)

    def list_analyses(self) -> list[AnalysisSummary]:
        summaries: list[AnalysisSummary] = []
        for child in self.base_dir.iterdir() if self.base_dir.is_dir() else []:
            if not child.is_dir() or not _ID_RE.match(child.name):
                continue
            summary_file = child / "analysis.json"
            if not summary_file.is_file():
                continue
            try:
                summaries.append(AnalysisSummary.model_validate(read_json(summary_file)))
            except Exception:
                continue
        summaries.sort(key=lambda item: item.created_at, reverse=True)
        return summaries

    def delete(self, analysis_id: str) -> None:
        import shutil

        root = self.analysis_dir(analysis_id)
        if not root.exists():
            raise AnalysisNotFoundError(
                f"Analysis '{analysis_id}' was not found.",
                hint="Call list_analyses to inspect cached ids.",
                details={"analysis_id": analysis_id},
            )
        shutil.rmtree(root, ignore_errors=True)

    # ------------------------------------------------------------ cache upkeep
    def _repo_dir(self) -> Path:
        return self.settings.resolved_cache_dir() / "repos"

    def cache_stats(self, *, include_entries: bool = True) -> CacheStats:
        """Return cache size/age statistics without modifying anything."""

        try:
            entries: list[CacheEntry] = []
            now = utc_now()
            for child in self.base_dir.iterdir() if self.base_dir.is_dir() else []:
                if not child.is_dir() or not _ID_RE.match(child.name):
                    continue
                summary_file = child / "analysis.json"
                if not summary_file.is_file():
                    continue
                try:
                    summary = AnalysisSummary.model_validate(read_json(summary_file))
                    created = _parse_iso(summary.created_at)
                except Exception:
                    continue
                age = (now - created).total_seconds() if created else 0.0
                entries.append(
                    CacheEntry(
                        analysis_id=summary.analysis_id,
                        project_path=summary.project_path,
                        framework=summary.framework,
                        created_at=summary.created_at,
                        age_seconds=age,
                        bytes=dir_size(child),
                    )
                )
            entries.sort(key=lambda item: item.created_at)
            repo_dir = self._repo_dir()
            created_values = [entry.created_at for entry in entries]
            return CacheStats(
                analyses=len(entries),
                total_bytes=sum(entry.bytes for entry in entries),
                repos=len([p for p in repo_dir.iterdir() if p.is_dir()]) if repo_dir.is_dir() else 0,
                repo_bytes=dir_size(repo_dir),
                oldest_created_at=created_values[0] if created_values else None,
                newest_created_at=created_values[-1] if created_values else None,
                entries=entries if include_entries else [],
            )
        except OSError as exc:  # pragma: no cover - defensive
            raise CacheError(
                f"Could not compute cache statistics: {exc}",
                hint="Check that PYOPENAPI_CACHE_DIR is readable and writable.",
            ) from exc

    def cleanup_cache(
        self,
        *,
        max_age_days: float | None = None,
        max_bytes: int | None = None,
        dry_run: bool = False,
        include_repos: bool = False,
    ) -> CacheStats:
        """Prune analyses older than ``max_age_days`` and enforce a size cap.

        Entries are removed oldest-first until the total size is under
        ``max_bytes``. With ``dry_run`` the report lists what *would* be removed
        but nothing is deleted.
        """

        import shutil

        age_limit = self.settings.cache_max_age_days if max_age_days is None else max_age_days
        byte_limit = self.settings.cache_max_bytes if max_bytes is None else max_bytes
        stats = self.cache_stats()
        removed: list[str] = []
        freed = 0

        entries = list(stats.entries)
        keep: list[CacheEntry] = []

        age_cutoff = max(0.0, float(age_limit)) * 86_400.0
        for entry in entries:
            if age_cutoff > 0 and entry.age_seconds > age_cutoff:
                removed.append(entry.analysis_id)
                freed += entry.bytes
            else:
                keep.append(entry)

        if byte_limit and byte_limit > 0:
            total = sum(entry.bytes for entry in keep)
            for entry in sorted(keep, key=lambda item: item.created_at):
                if total <= byte_limit:
                    break
                removed.append(entry.analysis_id)
                freed += entry.bytes
                total -= entry.bytes
                keep.remove(entry)

        if not dry_run:
            for analysis_id in removed:
                shutil.rmtree(self.base_dir / analysis_id, ignore_errors=True)
            if include_repos:
                repo_dir = self._repo_dir()
                if repo_dir.is_dir():
                    for child in repo_dir.iterdir():
                        if child.is_dir():
                            freed += dir_size(child)
                            shutil.rmtree(child, ignore_errors=True)

        final = self.cache_stats(include_entries=False)
        return final.model_copy(
            update={
                "removed": removed,
                "freed_bytes": freed,
                "dry_run": dry_run,
            }
        )

    # Backwards/CLI-friendly alias.
    def prune(self, **kwargs: Any) -> CacheStats:
        return self.cleanup_cache(**kwargs)


def _parse_iso(value: str) -> Any:
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------- filters
def filter_endpoints(
    endpoints: list[dict[str, Any]],
    *,
    method: str | None = None,
    path_prefix: str | None = None,
    search: str | None = None,
    view: str | None = None,
) -> list[dict[str, Any]]:
    results = endpoints
    if method:
        wanted = method.upper()
        results = [item for item in results if str(item.get("method", "")).upper() == wanted]
    if path_prefix:
        results = [item for item in results if _endpoint_path(item).startswith(path_prefix)]
    if view:
        needle = view.lower()
        results = [item for item in results if needle in str(item.get("view", "")).lower()]
    if search:
        needle = search.lower()
        results = [
            item
            for item in results
            if needle in _endpoint_path(item).lower()
            or needle in str(item.get("view", "")).lower()
            or needle in str(item.get("function", "")).lower()
        ]
    return results


def _endpoint_path(raw: dict[str, Any]) -> str:
    url = raw.get("url")
    if isinstance(url, dict):
        return str(url.get("url") or "")
    return str(url or "")


def summarize_endpoint(raw: dict[str, Any]) -> EndpointInfo:
    url = raw.get("url")
    path = _endpoint_path(raw)
    parameters_raw = url.get("parameter", []) if isinstance(url, dict) else []
    parameters: list[EndpointParameter] = []
    for item in parameters_raw or []:
        if not isinstance(item, dict):
            continue
        parameters.append(
            EndpointParameter(
                name=str(item.get("name", "")),
                location=str(item.get("location") or "path"),
                required=item.get("required"),
                type=item.get("type"),
                description=item.get("description"),
            )
        )
    metadata = {
        key: raw[key]
        for key in (
            "request_model",
            "response_model",
            "response_annotation",
            "request_serializer",
            "response_serializer",
            "description",
        )
        if raw.get(key) is not None
    }
    return EndpointInfo(
        method=str(raw.get("method", "")).upper(),
        path=path,
        view=raw.get("view"),
        function=raw.get("function"),
        is_viewset=bool(raw.get("is_viewset", False)),
        source_file=raw.get("path"),
        parameters=parameters,
        tags=[str(tag) for tag in raw.get("tags", []) if tag],
        summary=raw.get("summary"),
        request_model=raw.get("request_serializer") or raw.get("request_model"),
        response_model=raw.get("response_serializer") or raw.get("response_model"),
        response_many=bool(raw.get("response_many", False)),
        serializer=raw.get("serializer"),
        metadata=metadata,
    )


def summarize_component(name: str, raw: dict[str, Any]) -> ComponentInfo:
    parents = _normalise_parents(raw.get("parent_classes") or raw.get("parentClasses"))
    fields = _normalise_fields(raw)
    dependencies = _normalise_dependencies(raw.get("data_classes"))

    return ComponentInfo(
        name=name,
        kind=str(raw.get("type") or raw.get("kind") or "unknown"),
        source_file=raw.get("path"),
        parent_classes=parents,
        fields=fields,
        dependencies=dependencies,
        supports_request=raw.get("supports_request"),
        supports_response=raw.get("supports_response"),
        metadata={
            key: raw[key]
            for key in ("start_line", "end_line", "inner_classes")
            if raw.get(key) is not None
        },
    )


def _normalise_parents(value: Any) -> list[str]:
    if not value:
        return []
    parents: list[str] = []
    if isinstance(value, dict):
        for key in value:
            parents.append(str(key).split(".")[-1])
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("qualifiedName")
                if name:
                    parents.append(str(name).split(".")[-1])
            elif item:
                parents.append(str(item).split(".")[-1])
    return parents


def _normalise_dependencies(value: Any) -> list[str]:
    if not value:
        return []
    names: list[str] = []
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("qualifiedName")
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))
    return list(dict.fromkeys(names))


def _normalise_fields(raw: dict[str, Any]) -> list[FieldInfo]:
    fields: list[FieldInfo] = []
    properties = raw.get("properties") or raw.get("fields") or []
    assignments = raw.get("property_assignments") or {}

    if isinstance(properties, dict):
        properties = [
            {"name": key, **value} if isinstance(value, dict) else {"name": key, "type": value}
            for key, value in properties.items()
        ]

    for item in properties if isinstance(properties, (list, tuple)) else []:
        if isinstance(item, str):
            fields.append(
                FieldInfo(
                    name=item,
                    type=assignments.get(item),
                    source="class property",
                )
            )
        elif isinstance(item, dict):
            fields.append(
                FieldInfo(
                    name=str(item.get("name") or item.get("field") or "unknown"),
                    type=_stringify_type(item.get("type")),
                    format=item.get("format"),
                    ref=item.get("ref"),
                    enum=item.get("enum") if isinstance(item.get("enum"), list) else None,
                    items=item.get("items") if isinstance(item.get("items"), dict) else None,
                    additional_properties=(
                        item.get("additionalProperties")
                        if isinstance(item.get("additionalProperties"), dict)
                        else None
                    ),
                    max_length=item.get("max_length") if isinstance(item.get("max_length"), int) else None,
                    required=item.get("required"),
                    nullable=item.get("nullable"),
                    read_only=item.get("read_only"),
                    write_only=item.get("write_only"),
                    description=item.get("description"),
                    default=item.get("default"),
                    source=item.get("source"),
                )
            )
    return fields


def _stringify_type(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " | ".join(str(item) for item in value)
    if isinstance(value, dict):
        ref = value.get("ref") or value.get("$ref")
        if ref:
            return str(ref).split("/")[-1]
        type_name = value.get("type")
        if type_name:
            return str(type_name)
    return str(value)
