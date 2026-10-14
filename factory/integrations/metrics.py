"""Интеграция orch-metrics (наблюдаемость) — этап 3 дорожной карты.

Метрики фабрики отправляются в `orch-metrics`, если его шим найден; иначе
пишутся локально в `state/metrics.jsonl`. Так у оператора всегда есть поток
метрик, независимо от наличия внешнего коллектора.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.invr_tool import InvrToolAdapter


class MetricsIntegration:
    name = "orch-metrics"

    def __init__(self, settings):
        self.settings = settings
        self._invr = InvrToolAdapter(settings)

    def _base(self) -> Path:
        return Path(self.settings.path).parent.parent

    def _local_path(self) -> Path:
        return self._base() / str(self.settings.get("metrics.path", "state/metrics.jsonl"))

    def shim(self) -> Path | None:
        if not self.settings.get("adapters.metrics_enabled", True):
            return None
        return self._invr._shim(self.name)

    def available(self) -> bool:
        return self.shim() is not None

    def health(self) -> dict:
        shim = self.shim()
        info = {"integration": self.name, "available": shim is not None,
                "shim": str(shim) if shim else None,
                "sink": "orch-metrics" if shim else "local"}
        if shim is None:
            path = self._local_path()
            info["local_path"] = str(path)
            info["hint"] = ("шим orch-metrics не найден — метрики честно пишутся "
                            "в локальный приёмник state/metrics.jsonl")
        return info

    def emit(self, event: dict) -> dict:
        """Записать событие метрик: внешний коллектор или локальный файл."""
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        shim = self.shim()
        if shim is not None:
            try:
                proc = subprocess.run(self._argv(shim),
                                      input=json.dumps(record, ensure_ascii=False),
                                      capture_output=True, text=True, timeout=30)
                return {"ok": proc.returncode == 0, "sink": "orch-metrics",
                        "raw": (proc.stdout or "").strip()[-500:]}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "sink": "orch-metrics", "error": str(exc)}
        path = self._local_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True, "sink": "local", "file": f"{path.parent.name}/{path.name}"}

    @staticmethod
    def _argv(shim: Path) -> list[str]:
        if shim.suffix.lower() == ".ps1":
            shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            return [shell, "-NoProfile", "-File", str(shim)]
        return [str(shim)]
