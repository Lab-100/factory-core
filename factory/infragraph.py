"""Карта инфраструктуры станции — чтение состояния инструмента ``infra-graph`` (§4.7).

Карту узлов станции (сессии opencode, провайдеры, MCP-серверы, службы, пул
Lab100, каталог отката) ведёт инструмент ``infra-graph`` из склада INVR. Он же
знает классы узлов (core / service / agent / demand), их состояние
(``active`` / ``idle`` / ``unloaded`` / ``need-guard`` / ``fault``), очки,
интент, владельца, кандидатов на выгрузку и умеет отрисовать граф в PNG
(offline-рендер ``-Shot``).

Фабрика эту логику не дублирует, а читает готовое состояние:

* ``nodes.json`` (схема ``invr.nodes/1``) — узлы и кандидаты на выгрузку;
* ``events.log`` — кольцевой журнал причин переходов состояний;
* ``-Shot <png>`` — снимок графа для показа прямо в дашборде.

Если инструмент не найден, честно возвращается ``available: false`` с путями
и подсказкой — выдуманных узлов не бывает.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

#: Переменная инструмента: путь к ``nodes.json``.
STATE_ENV = "INFRAGRAPH_STATE_FILE"

#: Штатное место состояния, если переменная не задана.
DEFAULT_STATE = Path("monitor") / "state" / "nodes.json"

#: Имя файла снимка графа (кладём рядом с состоянием).
SHOT_NAME = "infra-graph.png"

#: PowerShell-шим инструмента: ``.cmd``-обёртка аргументы не передаёт,
#: поэтому снимок (``-Shot``) запускается только через ``.ps1``.
SHIM_PS1 = "infra-graph.ps1"

#: Порядок классов узлов для вывода (от ядра к инструментам по требованию).
CLASS_ORDER = ("core", "service", "agent", "demand")

#: Русские названия классов.
CLASS_TITLES = {
    "core": "ядро",
    "service": "служба",
    "agent": "агент",
    "demand": "по требованию",
    "unknown": "неизвестно",
}


def tools_root(settings=None) -> Path | None:
    """Корень склада инструментов INVR (переменная, затем настройка фабрики)."""
    raw = os.environ.get("INVR_TOOLS_ROOT") or ""
    if not raw and settings is not None:
        raw = str(settings.get("adapters.invr_root", "") or "")
    if not raw:
        # штатное место склада инструментов, если ни переменная, ни настройка не заданы
        home = Path.home() / ".devstation" / "tools"
        return home if home.exists() else None
    path = Path(raw)
    return path if path.exists() else None


def state_file(settings=None) -> Path:
    """Путь к ``nodes.json``: переменная инструмента, иначе склад INVR."""
    raw = os.environ.get(STATE_ENV) or ""
    if raw:
        return Path(raw)
    base = tools_root(settings)
    return (base / DEFAULT_STATE) if base is not None else DEFAULT_STATE


def events_file(settings=None) -> Path:
    """Путь к журналу переходов состояний (рядом с ``nodes.json``)."""
    return state_file(settings).with_name("events.log")


def shot_path(settings=None) -> Path:
    """Путь к снимку графа (рядом с состоянием инструмента)."""
    return state_file(settings).with_name(SHOT_NAME)


def shim(settings=None) -> Path | None:
    """Шим инструмента ``infra-graph`` в складе INVR (``.cmd``, затем ``.ps1``)."""
    base = tools_root(settings)
    if base is None:
        return None
    for name in ("infra-graph.cmd", "infra-graph.ps1"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def shim_ps1(settings=None) -> Path | None:
    """PowerShell-шим ``infra-graph.ps1`` — принимает аргументы (``-Shot``)."""
    base = tools_root(settings)
    if base is None:
        return None
    candidate = base / SHIM_PS1
    return candidate if candidate.exists() else None


def _tail(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()][-limit:]


def _candidates(raw) -> list[dict]:
    """Кандидаты на выгрузку: приводим dict/list к списку словарей."""
    items: list[dict] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                items.append({"id": str(key), **_flat(value)})
            else:
                items.append({"id": str(key), "detail": str(value)})
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict):
                items.append({"id": str(value.get("id") or value.get("Id") or "—"),
                              **_flat(value)})
            else:
                items.append({"id": str(value)})
    return items


def _flat(value: dict) -> dict:
    return {str(k): v for k, v in value.items() if str(k).lower() not in ("id",)}


def _node(node_id: str, item: dict) -> dict:
    points = float(item.get("Val") or 0.0)
    return {
        "id": str(node_id),
        "state": str(item.get("State") or "unknown"),
        "class": str(item.get("Class") or "unknown"),
        "points": round(points, 3),
        "points_percent": int(round(points * 100)),
        "owner": str(item.get("Owner") or ""),
        "intent": str(item.get("Intent") or ""),
        "wanted": bool(item.get("Wanted")),
        "fault": bool(item.get("Fault")),
        "busy": bool(item.get("Busy")),
        "errors": int(item.get("Err") or 0),
        "resolved": int(item.get("Resolved") or 0),
        "idle_sec": int(item.get("IdleSec") or 0),
        "pid": str(item.get("Pid") or ""),
        "last_reply": item.get("LastReply"),
    }


def load(settings=None, limit: int = 200) -> dict:
    """Прочитать карту: узлы, сводки по состояниям и классам, кандидаты, журнал."""
    path = state_file(settings)
    payload: dict = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}

    raw_nodes = payload.get("nodes") or {}
    if isinstance(raw_nodes, list):                     # терпимость к списку узлов
        raw_nodes = {str(n.get("id") or n.get("Id") or ""): n
                     for n in raw_nodes if isinstance(n, dict)}

    nodes = [_node(str(node_id), item)
             for node_id, item in raw_nodes.items() if isinstance(item, dict)]
    nodes.sort(key=lambda n: (
        CLASS_ORDER.index(n["class"]) if n["class"] in CLASS_ORDER else len(CLASS_ORDER),
        n["id"]))

    counts: dict[str, int] = {}
    classes: dict[str, int] = {}
    for node in nodes:
        counts[node["state"]] = counts.get(node["state"], 0) + 1
        classes[node["class"]] = classes.get(node["class"], 0) + 1

    tool_shim = shim(settings)
    shot = shot_path(settings)
    return {
        "available": bool(nodes),
        "schema": payload.get("schema"),
        "saved_at": payload.get("savedAt"),
        "path": str(path),
        "state_exists": path.exists(),
        "state_age_sec": _age_sec(path),
        "tools_root": str(tools_root(settings) or ""),
        "shim": str(tool_shim) if tool_shim else "",
        "nodes": nodes,
        "total": len(nodes),
        "counts": counts,
        "classes": classes,
        "class_titles": CLASS_TITLES,
        "candidates": _candidates(payload.get("candidates")),
        "events": _tail(events_file(settings), limit),
        "shot": str(shot),
        "shot_exists": shot.exists(),
        **_shot_stamp(shot),
    }


def _age_sec(path: Path) -> int | None:
    """Сколько секунд назад записано состояние (для «свежести» карты)."""
    try:
        return int(max(0.0, time.time() - path.stat().st_mtime))
    except OSError:
        return None


def _shot_stamp(shot: Path) -> dict:
    """Время снимка графа: epoch-метка, локальное время и свежесть.

    Нужно разделу «инфраструктура» в дашборде: показать, когда сделан снимок,
    и обновлять картинку только если он изменился (вместе с ETag/304).
    """
    try:
        mtime = shot.stat().st_mtime
    except OSError:
        return {"shot_mtime": 0, "shot_saved_at": "", "shot_age_sec": None}
    return {
        "shot_mtime": int(mtime),
        "shot_saved_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
        "shot_age_sec": int(max(0.0, time.time() - mtime)),
    }


def snapshot(settings=None, timeout: int = 240) -> dict:
    """Отрисовать карту в PNG (offline-рендер ``infra-graph -Shot``).

    Запускается только ``.ps1``-шим: ``.cmd``-обёртка аргументов не передаёт.
    """
    script = shim_ps1(settings)
    if script is None:
        return {"ok": False,
                "error": f"шим {SHIM_PS1} не найден в складе INVR"}
    target = shot_path(settings)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"каталог снимка недоступен: {exc}"}
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    argv = [shell, "-STA", "-NoProfile", "-File", str(script), "-Shot", str(target)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — снимок не должен ломать дашборд
        return {"ok": False, "error": str(exc)}
    ok = proc.returncode == 0 and target.exists()
    return {
        "ok": ok,
        "shot": str(target),
        "path": str(target),
        "raw": (proc.stdout or "").strip()[-600:],
        "error": "" if ok else ((proc.stderr or proc.stdout or "").strip()[-600:]),
    }
