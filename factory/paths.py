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