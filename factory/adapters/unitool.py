"""Адаптер единого интегрального инструмента unitool.

unitool объединяет три компонента (Lab100 — поиск, MultiTool — свободные
модели, LiteLLM/ai-gateway — шлюз пулов) за одним интерфейсом и одним
каскадом отказов. Адаптер вызывает CLI unitool: ``python -m unitool.cli
<команда> …``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .base import Adapter, StepResult
from ..paths import resolve_python


class UnitoolAdapter(Adapter):
    """Исполнитель unitool: единая точка входа к ИИ-инструментам Фабрики."""

    name = "unitool"

    # --- поиск каталога репозитория -------------------------------------
    def _dir(self) -> Path | None:
        """Каталог репозитория unitool (настройка, env, затем соседние каталоги)."""
        configured = str(self.settings.get("adapters.unitool_dir") or "")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))
        env_dir = os.environ.get("UNITOOL_DIR")
        if env_dir:
            candidates.append(Path(env_dir))
        root = self.settings.path.parent.parent  # .../factory-core
        candidates += [root.parent / "unitool", root / "unitool", Path.cwd() / "unitool"]
        for candidate in candidates:
            if candidate and (candidate / "unitool" / "cli.py").exists():
                return candidate
        return None

    def _python(self) -> str:
        """Интерпретатор для запуска CLI unitool."""
        return str(self.settings.get("adapters.unitool_python") or resolve_python())

    # --- сопоставление действий фабрики на команды unitool --------------
    def _command(self, action: str, params: dict) -> list[str]:
        """Собрать argv для CLI unitool по действию фабрики."""
        override = self.settings.get("adapters.unitool_cmd")
        if override:
            base = list(override) if isinstance(override, list) else str(override).split()
        else:
            # по умолчанию — интерпретатор и модуль пакета unitool
            base = [self._python(), "-m", "unitool.cli"]

        subcmd, argument = self._subcommand(action, params)
        argv = [*base, subcmd]
        if argument:
            argv.append(argument)

        if action in ("search", "ask", "research"):
            limit = params.get("limit")
            if limit:
                argv += ["--limit", str(int(limit))]
        if action == "search" and params.get("provider"):
            argv += ["--provider", str(params["provider"])]
        if action in ("ask", "research", "chat"):
            engine = params.get("engine")
            if engine:
                argv += ["--engine", str(engine)]
        if action in ("ask", "research") and params.get("model"):
            argv += ["--model", str(params["model"])]
        return argv

    @staticmethod
    def _subcommand(action: str, params: dict) -> tuple[str, str]:
        """Подкоманда unitool и её аргумент для действия фабрики.

        Действия фабрики: status | search | scrape | ask | research | chat |
        models | doctor. ``research`` и ``chat`` идут в unitool как ``ask``.
        """
        if action == "status":
            return "status", ""
        if action == "models":
            return "models", ""
        if action == "doctor":
            return "doctor", ""
        if action == "scrape":
            return "scrape", str(params.get("url") or params.get("query") or "")
        if action in ("ask", "research", "chat"):
            text = str(params.get("prompt") or params.get("query") or params.get("text") or "")
            return "ask", text
        # неизвестное действие — считаем поиском
        return "search", str(params.get("query") or params.get("prompt") or "")

    # --- доступность и здоровье ------------------------------------------
    def available(self) -> bool:
        """Готов ли адаптер: включён настройкой и найден каталог/команда."""
        if not self.settings.get("adapters.unitool_enabled", True):
            return False
        return self._dir() is not None or bool(self.settings.get("adapters.unitool_cmd"))

    def health(self) -> dict:
        """Диагностика для ``factory doctor``."""
        workdir = self._dir()
        return {
            "adapter": self.name,
            "available": self.available(),
            "dir": str(workdir) if workdir else None,
            "python": self._python(),
            "cmd_override": bool(self.settings.get("adapters.unitool_cmd")),
        }

    # --- выполнение ------------------------------------------------------
    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        """Выполнить действие через CLI unitool."""
        workdir = self._dir()
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"note": "dry-run", "argv": self._command(action, params)})
        if workdir is None and not self.settings.get("adapters.unitool_cmd"):
            return StepResult(False, "unavailable", self.name, action,
                              error="каталог unitool не найден (unitool/cli.py)")
        argv = self._command(action, params)
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if workdir is not None:
            # пакет лежит в корне репозитория unitool — добавляем его в PYTHONPATH
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{workdir}{os.pathsep}{existing}" if existing else str(workdir)
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
