"""Настройки фабрики: чтение, точечное изменение, запись.

Режим автоматизации (auto|semi) и выбор интерфейса (cli|web|tray|all)
хранятся здесь и меняются в меню настроек (CLI, веб-дашборд, трей).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tomlw
from .paths import repo_root


def _coerce(value: Any) -> Any:
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("on", "yes"):
            return True
        if low in ("off", "no"):
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
    return value


@dataclass
class Settings:
    path: Path
    data: dict

    def get(self, dotted: str, default: Any = None) -> Any:
        current: Any = self.data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, dotted: str, value: Any) -> Any:
        parts = dotted.split(".")
        current = self.data
        for part in parts[:-1]:
            nxt = current.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                current[part] = nxt
            current = nxt
        current[parts[-1]] = _coerce(value)
        return current[parts[-1]]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(tomlw.dumps(self.data), encoding="utf-8")

    # Удобные аксессоры
    @property
    def automation(self) -> str:
        return str(self.get("automation.mode", "auto"))

    @automation.setter
    def automation(self, value: str) -> None:
        self.set("automation.mode", value)

    @property
    def ui(self) -> str:
        return str(self.get("ui.mode", "all"))

    @ui.setter
    def ui(self, value: str) -> None:
        self.set("ui.mode", value)

    @property
    def checkpoints(self) -> list[str]:
        return list(self.get("automation.semi.checkpoints", []) or [])


def load(path: str | Path | None = None) -> Settings:
    if path is None:
        path = repo_root() / "config" / "settings.toml"
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Settings(path, data)
