r"""Определение корня репозитория фабрики и рабочих путей.

В собранном (PyInstaller) варианте корень — пользовательский рабочий каталог:
%LOCALAPPDATA%\FactoryLab100 (или $FACTORY_HOME). При первом запуске туда
копируются дефолтные config/schemas/examples из бандла.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _seed_bundled(root: Path) -> None:
    meipass = Path(getattr(sys, "_MEIPASS", ""))
    if not meipass.is_dir():
        return
    for name in ("config", "schemas", "examples", "docs"):
        src = meipass / name
        dst = root / name
        if src.is_dir() and not dst.exists():
            shutil.copytree(src, dst)
    manifest = meipass / "project.json"
    if manifest.is_file() and not (root / "project.json").exists():
        shutil.copy2(manifest, root / "project.json")


def user_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "FactoryLab100"


def repo_root(start: str | Path | None = None) -> Path:
    """Ищем корень по наличию config/settings.toml (вверх по дереву)."""
    if FROZEN:
        root = Path(os.environ.get("FACTORY_HOME") or user_root())
        root.mkdir(parents=True, exist_ok=True)
        _seed_bundled(root)
        return root
    candidates: list[Path] = []
    if start is not None:
        candidates.append(Path(start))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parent.parent)
    for candidate in candidates:
        current = candidate.resolve()
        for parent in [current, *current.parents]:
            if (parent / "config" / "settings.toml").exists():
                return parent
    return Path.cwd().resolve()


def resolve(root: Path, value: str | Path) -> Path:
    """Путь относительно корня, если не абсолютный."""
    path = Path(value)
    return path if path.is_absolute() else (root / path)


# Признаки склада инструментов INVR-Tools: шим resolve-tools или реестр версий.
_TOOLS_SHIMS = ("resolve-tools.ps1", "resolve-tools.cmd", "resolve-tools.bat",
                "resolve-tools.exe")


def _looks_like_tools_root(path: Path) -> bool:
    """Похож ли каталог на склад инструментов INVR-Tools."""
    try:
        if not path.is_dir():
            return False
    except OSError:
        return False
    if any((path / name).exists() for name in _TOOLS_SHIMS):
        return True
    return (path / "registry").is_dir()


def tools_root() -> Path | None:
    """Корень склада инструментов INVR-Tools.

    Сначала берётся из переменных окружения (``INVR_TOOLS_ROOT`` или
    ``DEVSTATION_TOOLS``) — так конфиг фабрики остаётся переносимым между
    машинами. Если переменные не заданы, склад ищется автоматически: рядом
    с корнем фабрики (``scripts-tools``, ``tools``) и в домашнем каталоге
    (``~/.devstation/tools``, ``~/.devstation``) — по наличию шима
    ``resolve-tools`` или каталога ``registry``. Пути к состоянию и журналам
    mcp-watchdog выводятся из него, а не прописываются в репозиторий.
    """
    for env_name in ("INVR_TOOLS_ROOT", "DEVSTATION_TOOLS"):
        value = os.environ.get(env_name)
        if value:
            return Path(value)
    base = repo_root()
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME")
                or str(Path.home()))
    for candidate in (
        base.parent / "scripts-tools",
        base.parent / "tools",
        base / "tools",
        home / ".devstation" / "tools",
        home / ".devstation",
    ):
        if _looks_like_tools_root(candidate):
            return candidate
    return None