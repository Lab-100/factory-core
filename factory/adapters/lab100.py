"""Адаптер пула ИИ-агентов Lab100 (реальный вызов `python -m lab100.cli`)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .base import Adapter, StepResult
from ..paths import resolve_python


class Lab100Adapter(Adapter):
    name = "lab100"

    # --- поиск каталога репозитория ---
    def _dir(self) -> Path | None:
        configured = str(self.settings.get("adapters.lab100_dir") or "")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))
        env_dir = os.environ.get("LAB100_DIR")
        if env_dir:
            candidates.append(Path(env_dir))
        root = self.settings.path.parent.parent  # .../factory-core
        candidates += [root.parent / "Lab100", root / "Lab100", Path.cwd() / "Lab100"]
        for candidate in candidates:
            if candidate and (candidate / "lab100" / "cli.py").exists():
                return candidate
        return None

    def _python(self) -> str:
        return str(self.settings.get("adapters.lab100_python") or resolve_python())

    # действие фабрики -> подкоманда Lab100 CLI (у CLI нет `research`)
    _SUBCMD = {"research": "ask"}

    def _command(self, action: str, params: dict) -> list[str]:
        override = self.settings.get("adapters.lab100_cmd")
        subcmd = self._SUBCMD.get(action, action)
        argument = self._argument(action, params)
        if override:
            base = list(override) if isinstance(override, list) else str(override).split()
            argv = [*base, subcmd, argument]
        else:
            argv = [self._python(), "-m", "lab100.cli", subcmd, argument]
        if action == "research":
            argv += ["--capability", str(params.get("capability") or "deep-research")]
        if action == "search" and not params.get("with_llm"):
            argv.append("--no-llm")
        if action == "search" and params.get("provider"):
            argv += ["--search", str(params["provider"])]
        return argv

    @staticmethod
    def _argument(action: str, params: dict) -> str:
        if action == "scrape":
            return str(params.get("url") or params.get("query") or "")
        return str(params.get("query") or params.get("prompt") or "")

    def available(self) -> bool:
        if not self.settings.get("adapters.lab100_enabled", True):
            return False
        return self._dir() is not None or bool(self.settings.get("adapters.lab100_cmd"))

    def health(self) -> dict:
        workdir = self._dir()
        return {
            "adapter": self.name,
            "available": self.available(),
            "dir": str(workdir) if workdir else None,
            "python": self._python(),
            "cmd_override": bool(self.settings.get("adapters.lab100_cmd")),
        }

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        workdir = self._dir()
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"note": "dry-run", "argv": self._command(action, params)})
        if workdir is None and not self.settings.get("adapters.lab100_cmd"):
            return StepResult(False, "unavailable", self.name, action,
                              error="каталог Lab100 не найден (lab100/cli.py)")
        argv = self._command(action, params)
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        try:
            proc = subprocess.run(argv, cwd=str(workdir) if workdir else None,
                                  capture_output=True, text=True, env=env,
                                  timeout=int(params.get("timeout_s", 180)))
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
        ok = proc.returncode == 0
        return StepResult(ok, "ok" if ok else "error", self.name, action,
                          {"argv": argv, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-2000:]},
                          None if ok else f"exit {proc.returncode}")
