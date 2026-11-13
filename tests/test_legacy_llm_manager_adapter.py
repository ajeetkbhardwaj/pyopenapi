from __future__ import annotations

import sys
import types
from typing import ClassVar

from pyopenapi.engines.legacy_llm_manager_adapter import LegacyLlmManagerAdapter


class FakeManager:
    instances: ClassVar[list[FakeManager]] = []

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self.calls: list[dict] = []
        FakeManager.instances.append(self)

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return '{"summary": "legacy ok", "confidence": 0.8}', 0


async def test_legacy_adapter_wraps_manager_generate(monkeypatch) -> None:
    common = types.ModuleType("common")
    common.__path__ = []  # type: ignore[attr-defined]
    common_llm = types.ModuleType("common.llm")
    common_llm.__path__ = []  # type: ignore[attr-defined]
    fake_module = types.ModuleType("common.llm.llm")
    fake_module.LLMManager = FakeManager  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setitem(sys.modules, "common.llm", common_llm)
    monkeypatch.setitem(sys.modules, "common.llm.llm", fake_module)

    adapter = LegacyLlmManagerAdapter(model="legacy-model", max_retries=1)
    output = await adapter.reason(
        system_prompt="system",
        prompt="user",
        schema={"type": "object", "required": ["summary"]},
        stage="reason_endpoints",
        item_id="endpoint-1",
    )

    assert output.data["summary"] == "legacy ok"
    assert output.provider == "legacy"
    assert output.model == "legacy-model"
    manager = FakeManager.instances[-1]
    assert manager.max_retries == 1
    call = manager.calls[-1]
    assert call["system_message"] == "system"
    assert call["prompt"] == "user"
    assert call["is_json"] is True
    assert call["agent_id"] == "reason_endpoints"
    assert call["entity_id"] == "endpoint-1"
