from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from .types import TelemetryRecord


@dataclass
class _Aggregate:
    latency_sum: float = 0.0
    latency_count: int = 0
    errors: int = 0
    successes: int = 0
    count: int = 0

    def add(self, record: TelemetryRecord) -> None:
        self.count += 1
        if record.success:
            self.successes += 1
        else:
            self.errors += 1
        if record.latency_ms is not None:
            self.latency_sum += float(record.latency_ms)
            self.latency_count += 1

    def as_dict(self) -> dict:
        return {
            "avg_latency_ms": (self.latency_sum / self.latency_count)
            if self.latency_count
            else None,
            "error_rate": (self.errors / self.count) if self.count else 0.0,
            "success_count": self.successes,
            "count": self.count,
        }


class TelemetryStore:
    """Append-only telemetry log with in-memory incremental aggregates.

    Records are persisted to disk for durability while summaries are computed
    from running aggregates, avoiding repeated full-file reads (previously
    O(n^2) across a long session).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._aggregates: dict[str, _Aggregate] = {}
        self._load_aggregates()

    def _load_aggregates(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = TelemetryRecord.model_validate_json(line)
                    except Exception:
                        continue
                    self._aggregates.setdefault(rec.model_id, _Aggregate()).add(rec)
        except OSError:
            return

    def append(self, record: TelemetryRecord) -> None:
        with self._lock:
            self._aggregates.setdefault(record.model_id, _Aggregate()).add(record)
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(record.model_dump_json() + "\n")
                    f.flush()
            except OSError:
                pass

    def summarize(self) -> dict[str, dict]:
        with self._lock:
            return {mid: agg.as_dict() for mid, agg in self._aggregates.items()}
