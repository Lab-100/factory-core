"""Сборка адаптеров фабрики."""
from __future__ import annotations

from .base import Adapter, StepResult
from .external_agent import ExternalAgentAdapter
from .invr_tool import InvrToolAdapter
from .lab100 import Lab100Adapter
from .llm_local import LocalLlmAdapter
from .region import RegionAdapter
from .web_chat import WebChatAdapter

__all__ = [
    "Adapter",
    "StepResult",
    "ExternalAgentAdapter",
    "InvrToolAdapter",
    "Lab100Adapter",
    "LocalLlmAdapter",
    "RegionAdapter",
    "WebChatAdapter",
    "build_adapters",
]


def build_adapters(settings) -> dict[str, Adapter]:
    adapters: list[Adapter] = [
        Lab100Adapter(settings),
        InvrToolAdapter(settings),
        LocalLlmAdapter(settings),
        ExternalAgentAdapter(settings),
        WebChatAdapter(settings),
        RegionAdapter(settings),
    ]
    return {adapter.name: adapter for adapter in adapters}
