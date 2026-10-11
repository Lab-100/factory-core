"""Проектные настройки фабрики — как в opencode/claude-code.

Проект опознаётся по файлу настроек, найденному вверх от текущего каталога:
``.factory/settings.toml``, ``.factory/factory.toml``, ``factory.toml``,
``factory.settings.toml`` (ближайший вверх). Значения проекта накладываются
поверх глобальных (``config/settings.toml``) глубоким слиянием — словари
объединяются рекурсивно, скаляры и списки проекта побеждают.

Модуль не обращается к сети и не зависит от внешних сервисов.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_NAMES = (
    ".factory/settings.toml",
    ".factory/factory.toml",
    "factory.toml",
    "factory.settings.toml",
)

TEMPLATE = """# Проектные настройки фабрики Lab-100 (перекрывают config/settings.toml).
# Примеры — раскомментируйте нужное:

# [automation]
# mode = "auto"

# [ui]
# lang = "ru"

# [operation]
# allow_external = true
"""


def find_project(start: str | Path | None = None) -> Path | None:
    """Ближайший вверх проектный файл настроек (или None)."""
    base = Path(start or os.getcwd()).resolve()
    candidates = [base, *base.parents]
    for directory in candidates:
        for name in CONFIG_NAMES:
            path = directory / name
            if path.is_file():
                return path
    return None


def load_project(path: str | Path) -> dict:
    """Чтение проектного файла (TOML)."""
    with Path(path).open("rb") as handle:
        return tomllib.load(handle)


def project_root(path: str | Path) -> Path:
    """Корень проекта для файла ``.factory/...`` или ``factory.toml``."""
    config = Path(path)
    if config.parent.name == ".factory":
        return config.parent.parent
    return config.parent


def deep_merge(base: dict, layer: dict) -> dict:
    """Глубокое слияние: словари объединяются рекурсивно, скаляры/списки layer побеждают."""
    result = dict(base)
    for key, value in (layer or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def apply_project(settings, path: str | Path | None = None, start: str | Path | None = None) -> dict:
    """Наложить проектный слой на настройки; вернуть применённый слой.

    Если ``path`` не задан — ищет ближайший проектный файл от ``start`` (по
    умолчанию — текущий каталог). Отсутствие проекта не является ошибкой.
    """
    config = Path(path) if path else find_project(start)
    if config is None or not Path(config).is_file():
        return {}
    layer = load_project(config)
    merged = deep_merge(settings.data, layer)
    settings.data.clear()
    settings.data.update(merged)
    settings.project_path = Path(config)
    return layer


def init_project(directory: str | Path, *, force: bool = False) -> Path:
    """Создать ``.factory/settings.toml`` в каталоге; вернуть путь файла."""
    base = Path(directory)
    target = base / ".factory" / "settings.toml"
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE, encoding="utf-8")
    return target
