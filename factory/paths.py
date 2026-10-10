"""Определение корня репозитория фабрики и рабочих путей."""
from __future__ import annotations

from pathlib import Path


def repo_root(start: str | Path | None = None) -> Path:
    """Ищем корень по наличию config/settings.toml (вверх по дереву)."""
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
