"""Адаптер склада инструментов INVR-Tools (шимы scripts-tools)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import Adapter, StepResult


class InvrToolAdapter(Adapter):
    name = "invr"

    def _root(self) -> Path | None:
        configured = self.settings.get("adapters.invr_root") or os.environ.get("INVR_TOOLS_ROOT", "")
        if configured:
            path = Path(configured)
            return path if path.exists() else None
        return None

    def available(self) -> bool:
        return bool(self.settings.get("adapters.invr_enabled", True))

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        tool = params.get("tool")
        root = self._root()
        shim = (root / f"{tool}.ps1") if (root and tool) else None
        if dry_run or shim is None or not shim.exists():
            note = "dry-run" if dry_run else "шим/корень INVR не найдены"
            return StepResult(True, "simulated", self.name, action,
                              {"note": note, "tool": tool, "args": params.get("args", [])})
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return StepResult(False, "unavailable", self.name, action, error="pwsh/powershell не найдены")
        argv = [shell, "-NoProfile", "-File", str(shim), *[str(a) for a in params.get("args", [])]]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=int(params.get("timeout_s", 180)))
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
        ok = proc.returncode == 0
        return StepResult(ok, "ok" if ok else "error", self.name, action,
                          {"stdout": proc.stdout[-2000:], "stderr": proc.stderr[-1000:]},
                          None if ok else f"exit {proc.returncode}")
