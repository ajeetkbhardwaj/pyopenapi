from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, path: Path, ttl_s: int = 300) -> None:
        self.path = path
        self.ttl_s = ttl_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _save(self) -> None:
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)

    @staticmethod
    def make_key(model: str, prompt: str, system: str | None, temperature: float, task_type: str) -> str:
        raw = json.dumps(
            {"m": model, "p": prompt, "s": system or "", "t": temperature, "tt": task_type},
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            if time.time() - item.get("ts", 0) > self.ttl_s:
                self._data.pop(key, None)
                self._save()
                return None
            return item.get("value")

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = {"ts": time.time(), "value": value}
            self._save()
