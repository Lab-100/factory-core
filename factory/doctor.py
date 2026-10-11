"""Диагностика фабрики: доступность адаптеров и интеграций.

Адаптеры — исполнители (Lab100, INVR-инструменты, локальная модель,
region-spoof). Интеграции — внешние инструменты наблюдаемости (backup-util,
orch-metrics, infra-graph); у каждой есть шим или честный локальный приёмник,
поэтому «недоступна» здесь означает лишь работу через локальный журнал.
"""
from __future__ import annotations

from .adapters import build_adapters
from .integrations import build_integrations


def run_doctor(settings) -> dict:
    """Снимок здоровья: списки адаптеров и интеграций."""
    adapters = build_adapters(settings)
    integrations = build_integrations(settings)
    return {
        "adapters": [adapter.health() for adapter in adapters.values()],
        "integrations": [integration.health() for integration in integrations.values()],
    }


def summarize(rows: list[dict]) -> tuple[int, int]:
    """Совместимость: (готово, всего) для любого списка строк здоровья."""
    ready = sum(1 for row in rows if row.get("available"))
    return ready, len(rows)
