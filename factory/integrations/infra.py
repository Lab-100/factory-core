"""Интеграция infra-graph (карта сервисов и потока работ) — §4.7.

Ход задачи (домены, адаптеры, инструменты) отправляется в `infra-graph`, если
его шим найден; иначе пишется локально в `state/infra.jsonl`. Так поток работ
всегда зафиксирован, независимо от наличия внешнего инструмента — та же
честная деградация, что у `orch-metrics` и `backup-util`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.invr_tool import InvrToolAdapter


class InfraIntegration:
    name = "infra-graph"

    def __init__(self, settings):
        self.settings = settings
        self._invr = InvrToolAdapter(settings)

    def _base(self) -> Path:
        return Path(self.settings.path).parent.parent

    def _local_path(self) -> Path:
        return self._base() / str(self.settings.get("infra.path", "state/infra.jsonl"))

    def shim(self) -> Path | None:
        if not self.settings.get("adapters.infra_enabled", True):
            return None
        return self._invr._shim(self.name)

    def available(self) -> bool:
        return self.shim() is not None

    def health(self) -> dict:
        return {"integration": self.name, "available": self.available(),
                "shim": str(self.shim()) if self.shim() else None,
                "sink": "infra-graph" if self.shim() else "local"}

    def emit(self, event: dict) -> dict:
        """Записать событие потока: внешний infra-graph или локальный файл."""
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        shim = self.shim()
        if shim is not None:
            try:
                proc = subprocess.run(self._argv(shim),
                                      input=json.dumps(record, ensure_ascii=False),
                                      capture_output=True, text=True, timeout=30)
                return {"ok": proc.returncode == 0, "sink": "infra-graph",
                        "raw": (proc.stdout or "").strip()[-500:]}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "sink": "infra-graph", "error": str(exc)}
        path = self._local_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True, "sink": "local", "file": f"{path.parent.name}/{path.name}"}

    @staticmethod
    def _argv(shim) -> list[str]:
        if str(shim).lower().endswith(".ps1"):
            import shutil as _shutil

            shell = _shutil.which("pwsh") or _shutil.which("powershell") or "powershell"
            return [shell, "-NoProfile", "-File", str(shim)]
        return [str(shim)]
