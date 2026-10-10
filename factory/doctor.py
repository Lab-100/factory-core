"""Диагностика фабрики: доступность адаптеров и внешних зависимостей."""
from __future__ import annotations

from .adapters import build_adapters


def run_doctor(settings) -> list[dict]:
    adapters = build_adapters(settings)
    return [adapter.health() for adapter in adapters.values()]


def summarize(rows: list[dict]) -> tuple[int, int]:
    ready = sum(1 for row in rows if row.get("available"))
    return ready, len(rows)
