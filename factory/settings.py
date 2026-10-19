"""Настройки фабрики: чтение, точечное изменение, запись.

Режим автоматизации (auto|semi) и выбор интерфейса (cli|web|tray|all)
хранятся здесь и меняются в меню настроек (CLI, веб-дашборд, трей).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import tomlpatch, tomlw
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
    _pending: list = field(default_factory=list)
    project_path: "Path | None" = None

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
        coerced = _coerce(value)
        current[parts[-1]] = coerced
        self._pending = [item for item in self._pending if item[0] != dotted]
        self._pending.append((dotted, coerced))
        return coerced

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(tomlw.dumps(self.data), encoding="utf-8")
            self._pending.clear()
            return
        for dotted, value in self._pending:
            tomlpatch.apply(self.path, dotted, value)
        self.data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        self._pending.clear()

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

    @property
    def lang(self) -> str:
        from .i18n import DEFAULT_LANG

        return str(self.get("ui.lang", DEFAULT_LANG))


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Глубокое слияние: значения overlay перекрывают base вложенно."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str | Path | None = None) -> Settings:
    if path is None:
        path = repo_root() / "config" / "settings.toml"
    path = Path(path)
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # Локальный слой поверх: machine-specific пути (персональные команды,
    # каталоги этой машины) не коммитятся в репозиторий, а живут в
    # state/config/settings.toml (state/ в .gitignore). Сливаете глубже.
    overlay = path.parent.parent / "state" / "config" / "settings.toml"
    if overlay.exists():
        data = _deep_merge(data, tomllib.loads(overlay.read_text(encoding="utf-8")))
    return Settings(path, data)
