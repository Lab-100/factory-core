"""Интеграция netsuite (сервис средств обхода) — наблюдаемость связки.

Сервис сетевой связки Lab-100 (:mod:`factory.netsuite`) управляет тремя
средствами: region-spoof, ai-gateway и my-openai-gemini. Эта интеграция
ничего не запускает и не останавливает — она только сообщает в разделе
«Диагностика» (``factory doctor``, веб-панель), настроен ли сервис, сколько
средств включено/запущено и какие есть замечания.

Если секция ``[netsuite]`` в настройках не заполнена, интеграция честно
отвечает «недоступна» с подсказкой — как и остальные интеграции фабрики,
она деградирует, а не врёт.
"""
from __future__ import annotations

from .. import netsuite


class NetSuiteIntegration:
    name = "netsuite"

    def __init__(self, settings):
        self.settings = settings

    def shim(self):
        """Внешних шимов нет: сервис — часть фабрики, поэтому всегда None."""
        return None

    def available(self) -> bool:
        """Готов ли сервис: настроен и хотя бы одно средство реально запускаемо."""
        if self.settings is None:
            return False
        if not self.settings.get("netsuite"):
            return False
        summary = netsuite.summary(self.settings)
        return bool(summary.get("configured")) and bool(summary.get("ready"))

    def health(self) -> dict:
        """Сводка состояния связки для раздела «Диагностика»."""
        summary = netsuite.summary(self.settings)
        return {
            "integration": self.name,
            "available": bool(summary.get("configured")) and bool(summary.get("ready")),
            "shim": None,
            "sink": "netsuite",
            "configured": bool(summary.get("configured")),
            "tools": summary.get("tools", 0),
            "on": summary.get("on", 0),
            "running": summary.get("running", 0),
            "applied": summary.get("applied", 0),
            "issues": list(summary.get("issues") or []),
            "hint": str(summary.get("hint") or ""),
        }

    def emit(self, event: dict) -> dict:
        """Наблюдаемость связки: событие уходит в журнал состояния сервиса.

        Сервис не рассылает событий во внешние инструменты (ядро без UI и
        без логирования), поэтому событие лишь подтверждается — чтобы
        потребители (:mod:`factory.dispatch`) не падали по неизвестности.
        """
        return {"ok": True, "sink": "netsuite", "event": str((event or {}).get("kind", ""))}
