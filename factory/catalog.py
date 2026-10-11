"""Каталог артефактов фабрики (§5.6): результаты задач по доменам.

Диспетчер складывает итог каждой задачи в
`catalog/<домен>/<slug>/` (`task.json`, `result.json`, `report.md`) либо в путь
из `outputs.catalog`. Этот модуль даёт оператору доступ к каталогу: список
записей по доменам и чтение отчёта — из CLI (`factory catalog`) и веб-дашборда.
Ничего не пишет, только читает (каталог наполняет диспетчер).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Поле вывода, по которому опознаём запись каталога среди прочих файлов.
RESULT_FILE = "result.json"


def base_dir(settings) -> Path:
    """Корень каталога: <корень фабрики>/<настройка catalog_dir>."""
    root = Path(settings.path).parent.parent
    return root / str(settings.get("catalog_dir", "catalog"))


def _load_json(path: Path) -> dict:
    """Терпимое чтение JSON: битый/пустой файл — пустой словарь."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _relative(folder: Path, base: Path) -> str:
    try:
        return folder.relative_to(base).as_posix()
    except ValueError:
        return folder.as_posix()


def entries(settings) -> list[dict]:
    """Все записи каталога (новые — первыми). Скан по файлам result.json."""
    base = base_dir(settings)
    if not base.exists():
        return []
    found: list[dict] = []
    for result in base.rglob(RESULT_FILE):
        if not result.is_file():
            continue
        folder = result.parent
        data = _load_json(result)
        task = data.get("task") if isinstance(data.get("task"), dict) else {}
        rel = _relative(folder, base)
        parts = rel.split("/")
        try:
            updated = datetime.fromtimestamp(
                result.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            updated = ""
        found.append({
            "domain": parts[0] if len(parts) > 1 else "",
            "name": parts[-1],
            "path": rel,
            "ok": data.get("ok"),
            "stopped": bool(data.get("stopped")),
            "goal": str(task.get("goal", "")),
            "automation": data.get("automation"),
            "steps": len(data.get("results") or []),
            "has_report": (folder / "report.md").exists(),
            "updated": updated,
        })
    found.sort(key=lambda item: item["updated"], reverse=True)
    return found


def build(settings) -> dict:
    """Сводка каталога: база, счётчики по доменам и список записей."""
    items = entries(settings)
    domains: dict[str, int] = {}
    for item in items:
        key = item["domain"] or "-"
        domains[key] = domains.get(key, 0) + 1
    return {
        "schema": "invr.catalog/1",
        "base": str(base_dir(settings)),
        "count": len(items),
        "domains": domains,
        "items": items,
    }


def _resolve(settings, path: str) -> Path | None:
    """Безопасно привести путь записи к каталогу внутри базы."""
    base = base_dir(settings).resolve()
    raw = Path(path)
    candidate = raw.resolve() if raw.is_absolute() else (base / path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def report(settings, path: str) -> str | None:
    """Прочитать report.md записи каталога (None — не найдено/вне каталога)."""
    candidate = _resolve(settings, path)
    if candidate is None:
        return None
    report_file = candidate / "report.md"
    try:
        return report_file.read_text(encoding="utf-8")
    except OSError:
        return None
