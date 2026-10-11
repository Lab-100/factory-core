"""Журналы арсенала — просмотр по каждому элементу и сводно.

Фабрика сводит в один просмотрщик журналы всех источников окружения:

  * фабрика      — ``state/ledger.jsonl``, ``state/watchdog.jsonl``,
                   ``state/supervisor.jsonl``, метрики и карта потока;
  * mcp-watchdog — ``state/events.log`` и ``Logs/mcp-watchdog.log``;
  * OpenCode     — ``log/opencode.log`` (каталог данных OpenCode);
  * службы       — журналы, указанные в ``[services.<name>].log``.

Чтение только последних строк (хвост), без загрузки файла целиком. Формат
строк не навязывается: JSONL разбирается (для фильтра по элементу), обычный
текст фильтруется подстрокой. Внешние процессы не запускаются.
"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import repo_root, tools_root

# Источники «ядра» фабрики: (id, имя, ключ настройки пути, значение по умолчанию)
CORE_SOURCES = (
    ("factory.ledger", "Журнал фабрики", "ledger_path", "state/ledger.jsonl"),
    ("factory.watchdog", "Супервизия (watchdog)", None, "state/watchdog.jsonl"),
    ("factory.supervisor", "Супервизия арсенала", None, "state/supervisor.jsonl"),
    ("factory.metrics", "Метрики (local)", "metrics.path", "state/metrics.jsonl"),
    ("factory.infra", "Карта потока (local)", "infra.path", "state/infra.jsonl"),
)


def _root(settings) -> Path:
    if settings is not None:
        return Path(settings.path).parent.parent
    return repo_root()


def _cfg(settings, key: str, default):
    if settings is None:
        return default
    return settings.get(key, default)


def _opencode_log(settings) -> Path:
    configured = str(_cfg(settings, "logs.opencode_log", "") or "").strip()
    if configured:
        return Path(configured)
    data_dir = str(_cfg(settings, "opencode.data_dir", "") or "").strip()
    base = Path(data_dir) if data_dir else (Path.home() / ".local" / "share" / "opencode")
    return base / "log" / "opencode.log"


def sources(settings=None) -> list[dict]:
    """Список известных журналов с признаком доступности."""
    root = _root(settings)
    out: list[dict] = []

    def add(sid: str, name: str, path: Path, kind: str) -> None:
        out.append({
            "id": sid,
            "name": name,
            "path": str(path),
            "kind": kind,
            "available": path.is_file(),
        })

    for sid, name, key, default in CORE_SOURCES:
        rel = _cfg(settings, key, default) if key else default
        add(sid, name, root / str(rel), "jsonl")

    # mcp-watchdog: рядом с файлом состояния и в Logs/
    estado = str(_cfg(settings, "sources.mcp_watchdog_state", "") or "").strip()
    wlog = str(_cfg(settings, "sources.mcp_watchdog_log", "") or "").strip()
    if not estado and not wlog:
        # переносимо: выводим из корня склада инструментов (INVR_TOOLS_ROOT)
        store = tools_root()
        if store is not None:
            estado = str(store / "mcp-watchdog" / "state" / "current.json")
            wlog = str(store / "Logs" / "mcp-watchdog.log")
    if estado:
        state_dir = Path(estado).parent
        add("mcp-watchdog.events", "mcp-watchdog (события)",
            state_dir / "events.log", "text")
        default_log = state_dir.parent / "Logs" / "mcp-watchdog.log"
        add("mcp-watchdog.log", "mcp-watchdog (журнал)",
            Path(wlog) if wlog else default_log, "text")
    elif wlog:
        add("mcp-watchdog.log", "mcp-watchdog (журнал)", Path(wlog), "text")

    add("opencode.log", "OpenCode", _opencode_log(settings), "text")

    # журналы служб, явно указанные в настройках
    services = _cfg(settings, "services", {}) or {}
    if isinstance(services, dict):
        for name, spec in services.items():
            if isinstance(spec, dict) and spec.get("log"):
                add(f"service:{name}", f"Служба {name}", Path(str(spec["log"])), "text")
    return out


def _read_tail(path: Path, limit: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-max(1, int(limit)):]]


def _matches(line: str, element: str | None, level: str | None) -> bool:
    if element and element.lower() not in line.lower():
        return False
    if level:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return level.lower() in line.lower()
        if isinstance(record, dict):
            value = str(record.get("level") or record.get("status")
                        or record.get("kind") or "").lower()
            return level.lower() in value
        return level.lower() in line.lower()
    return True


def tail(settings=None, source: str | None = None, limit: int = 200,
         element: str | None = None, level: str | None = None) -> dict:
    """Хвост одного источника (или всех) с фильтрами по элементу и уровню."""
    limit = int(limit or 200)
    known = sources(settings)
    selected = [s for s in known if source is None or s["id"] == source]

    lines: list[dict] = []
    for entry in selected:
        for raw in _read_tail(Path(entry["path"]), limit):
            if not _matches(raw, element, level):
                continue
            lines.append({"source": entry["id"], "name": entry["name"], "line": raw})

    available_ids = {s["id"] for s in known if s["available"]}
    if source is not None and source not in {s["id"] for s in known}:
        return {
            "schema": "invr.logs/1",
            "source": source,
            "available": False,
            "error": f"источник не найден: {source}",
            "lines": [],
            "count": 0,
            "sources": known,
        }
    return {
        "schema": "invr.logs/1",
        "source": source,
        "element": element,
        "level": level,
        "available": (source in available_ids) if source else bool(available_ids),
        "lines": lines[-limit:],
        "count": len(lines[-limit:]),
        "sources": known,
    }


def aggregate(settings=None, limit: int = 200, element: str | None = None,
              level: str | None = None) -> dict:
    """Сводный хвост по всем источникам (для дашборда «все журналы»)."""
    return tail(settings, source=None, limit=limit, element=element, level=level)
