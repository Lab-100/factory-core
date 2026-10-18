"""Интеграции фабрики с инструментами репозитория scripts-tools.

  * :class:`BackupIntegration`  — backup-util: учёт изменений (ChangeId);
  * :class:`MetricsIntegration` — orch-metrics: наблюдаемость (метрики задач);
  * :class:`InfraIntegration`   — infra-graph: карта сервисов и потока работ;
  * :class:`NetSuiteIntegration`— netsuite: сервис средств обхода (регион,
                                  шлюз маршрутизации, прокси Gemini→OpenAI).

Все интеграции работают по принципу «есть шим — зовём его, нет — не падаем»:
при отсутствии инструмента пишем локальный эквивалент, чтобы единый журнал
фабрики оставался полным. Это не заглушки, а честная деградация.
"""
from __future__ import annotations

from .backup import BackupIntegration
from .infra import InfraIntegration
from .metrics import MetricsIntegration
from .netsuite import NetSuiteIntegration

__all__ = ["BackupIntegration", "InfraIntegration", "MetricsIntegration",
           "NetSuiteIntegration", "build_integrations"]


def build_integrations(settings) -> dict:
    return {
        "backup": BackupIntegration(settings),
        "metrics": MetricsIntegration(settings),
        "infra": InfraIntegration(settings),
        "netsuite": NetSuiteIntegration(settings),
    }
