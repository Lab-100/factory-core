"""Интеграции фабрики с инструментами репозитория scripts-tools.

  * :class:`BackupIntegration`  — backup-util: учёт изменений (ChangeId);
  * :class:`MetricsIntegration` — orch-metrics: наблюдаемость (метрики задач).

Обе интеграции работают по принципу «есть шим — зовём его, нет — не падаем»:
при отсутствии инструмента пишем локальный эквивалент, чтобы единый журнал
фабрики оставался полным. Это не заглушки, а честная деградация.
"""
from __future__ import annotations

from .backup import BackupIntegration
from .metrics import MetricsIntegration

__all__ = ["BackupIntegration", "MetricsIntegration", "build_integrations"]


def build_integrations(settings) -> dict:
    return {
        "backup": BackupIntegration(settings),
        "metrics": MetricsIntegration(settings),
    }
