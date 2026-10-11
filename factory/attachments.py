"""Вложения фабрики — файлы, прикрепляемые оператором к запросу.

Файлы складываются в ``state/uploads/<scope>/<имя>`` (scope — сессия, заказ или
``default``). Имя санитизируется: отрезаются каталоги и подозрительные символы,
при совпадении имя уникализируется суффиксом ``-1``, ``-2`` и т. д.

Модуль не зависит от внешних сервисов и не обращается к сети.
"""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SCOPE = "default"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 МиБ


def safe_name(name: str, limit: int = 120) -> str:
    """Безопасное имя файла: без каталогов, без управляющих символов."""
    base = Path(str(name or "")).name.replace("\\", "_")
    base = re.sub(r"[^\w.\- ]+", "_", base, flags=re.UNICODE).strip(" .")
    return (base or "file")[:limit]


def safe_scope(scope: str) -> str:
    """Безопасное имя области (сессия/заказ): только буквы, цифры, ``-_.``."""
    cleaned = re.sub(r"[^\w.-]+", "_", str(scope or ""), flags=re.UNICODE).strip("._-")
    return (cleaned or DEFAULT_SCOPE)[:80]


class AttachmentsStore:
    """Файловое хранилище вложений оператора."""

    def __init__(self, root: str | Path, max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = Path(root)
        self.dir = self.root / "state" / "uploads"
        self.max_bytes = int(max_bytes)

    # --- пути ---
    def scope_dir(self, scope: str) -> Path:
        return self.dir / safe_scope(scope)

    def _unique(self, directory: Path, name: str) -> Path:
        candidate = directory / name
        if not candidate.exists():
            return candidate
        stem, suffix = candidate.stem, candidate.suffix
        for index in range(1, 10000):
            candidate = directory / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        return directory / f"{stem}-{len(list(directory.iterdir()))}{suffix}"

    # --- операции ---
    def save(self, scope: str, name: str, data: bytes) -> dict:
        """Сохранить файл; вернуть метаданные (имя, относительный путь, размер)."""
        if len(data) > self.max_bytes:
            raise ValueError(f"файл больше допустимого ({self.max_bytes} байт)")
        directory = self.scope_dir(scope)
        directory.mkdir(parents=True, exist_ok=True)
        path = self._unique(directory, safe_name(name))
        path.write_bytes(data)
        rel = path.relative_to(self.root).as_posix()
        return {"name": path.name, "path": rel, "size": len(data),
                "scope": safe_scope(scope)}

    def list(self, scope: str) -> list[dict]:
        directory = self.scope_dir(scope)
        if not directory.is_dir():
            return []
        items: list[dict] = []
        for path in sorted(directory.iterdir()):
            if path.is_file():
                items.append({
                    "name": path.name,
                    "path": path.relative_to(self.root).as_posix(),
                    "size": path.stat().st_size,
                    "scope": safe_scope(scope),
                })
        return items

    def resolve(self, rel_path: str) -> Path | None:
        """Абсолютный путь по относительному (защита от выхода за пределы)."""
        target = (self.root / str(rel_path or "")).resolve()
        base = self.dir.resolve()
        if target == base or base in target.parents:
            return target
        return None

    def delete(self, name: str, scope: str = DEFAULT_SCOPE) -> bool:
        path = self.scope_dir(scope) / safe_name(name)
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True
