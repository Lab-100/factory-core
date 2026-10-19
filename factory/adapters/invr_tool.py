"""Адаптер склада инструментов INVR-Tools (шимы scripts-tools)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import Adapter, StepResult

SHIM_SUFFIXES = (".ps1", ".cmd", ".bat", ".exe")


class InvrToolAdapter(Adapter):
    name = "invr"

    def _root(self) -> Path | None:
        # 1) явно заданные пути принимаем как есть
        explicit: list[Path] = []
        configured = str(self.settings.get("adapters.invr_root") or "")
        if configured:
            explicit.append(Path(configured))
        for env_name in ("INVR_TOOLS_ROOT", "DEVSTATION_TOOLS"):
            value = os.environ.get(env_name)
            if value:
                explicit.append(Path(value))
        for candidate in explicit:
            if candidate and candidate.exists():
                return candidate
        # 2) автопоиск рядом — только если там реально есть шимы
        root = self.settings.path.parent.parent  # .../factory-core
        home = Path(os.environ.get("USERPROFILE", str(Path.home())))
        near = [
            root.parent / "scripts-tools",
            root.parent / "tools",
            home / ".devstation" / "tools",
            home / ".devstation",
        ]
        for candidate in near:
            if candidate and candidate.exists() and any(
                (candidate / ("resolve-tools" + suffix)).exists() for suffix in SHIM_SUFFIXES
            ):
                return candidate
        return None

    def _shim(self, tool: str) -> Path | None:
        root = self._root()
        if not root or not tool:
            return None
        for suffix in SHIM_SUFFIXES:
            candidate = root / f"{tool}{suffix}"
            if candidate.exists():
                return candidate
        return None

    def available(self) -> bool:
        return bool(self.settings.get("adapters.invr_enabled", True)) and self._root() is not None

    def health(self) -> dict:
        root = self._root()
        tools = []
        if root:
            tools = sorted({p.stem for p in root.iterdir() if p.suffix in SHIM_SUFFIXES})[:40] \
                if root.exists() else []
        return {
            "adapter": self.name,
            "available": self.available(),
            "root": str(root) if root else None,
            "shims": tools,
        }

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        tool = params.get("tool")
        shim = self._shim(tool) if tool else None
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"note": "dry-run", "tool": tool, "args": params.get("args", [])})
        if shim is None:
            return StepResult(False, "unavailable", self.name, action,
                              error=f"шим не найден: {tool!r} (корень INVR: {self._root()})")
        if shim.suffix == ".ps1":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                return StepResult(False, "unavailable", self.name, action,
                                  error="pwsh/powershell не найдены")
            argv = [shell, "-NoProfile", "-File", str(shim), *[str(a) for a in params.get("args", [])]]
        else:
            argv = [str(shim), *[str(a) for a in params.get("args", [])]]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=int(params.get("timeout_s", 180)))
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
        ok = proc.returncode == 0
        return StepResult(ok, "ok" if ok else "error", self.name, action,
                          {"argv": argv, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-2000:]},
                          None if ok else f"exit {proc.returncode}")
