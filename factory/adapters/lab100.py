"""Адаптер пула ИИ-агентов Lab100 (поиск/исследование/скрейпинг)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .base import Adapter, StepResult


class Lab100Adapter(Adapter):
    name = "lab100"

    def _dir(self) -> Path | None:
        configured = self.settings.get("adapters.lab100_dir") or ""
        if configured:
            path = Path(configured)
            return path if path.exists() else None
        root = self.settings.path.parent.parent  # .../factory-core/config -> .../factory-core
        candidates = [root.parent / "Lab100", root / "Lab100", Path.cwd() / "Lab100"]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def available(self) -> bool:
        return bool(self.settings.get("adapters.lab100_enabled", True))

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        workdir = self._dir()
        if dry_run or workdir is None:
            note = "dry-run" if dry_run else "каталог Lab100 не найден"
            return StepResult(True, "simulated", self.name, action,
                              {"note": note, "query": params.get("query", ""), "action": action})
        query = str(params.get("query", ""))
        argv = [sys.executable, "-m", "lab100", action, query]
        try:
            proc = subprocess.run(argv, cwd=str(workdir), capture_output=True, text=True,
                                  timeout=int(params.get("timeout_s", 180)))
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
        ok = proc.returncode == 0
        return StepResult(ok, "ok" if ok else "error", self.name, action,
                          {"stdout": proc.stdout[-2000:], "stderr": proc.stderr[-1000:]},
                          None if ok else f"exit {proc.returncode}")
