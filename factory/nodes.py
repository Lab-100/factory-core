"""Узлы арсенала — состояния и проверки работоспособности.

Арсенал (:mod:`factory.arsenal`) отвечает на вопрос «что у нас есть»; этот
модуль — на вопрос «что из этого работает прямо сейчас». Для каждого элемента
исполняется его ``probe`` и выводится состояние:

  * ``active``     — работает (проверка прошла);
  * ``idle``       — не запущен, но вмешательство не нужно (владелец поднимет);
  * ``unloaded``   — не развёрнут/выключен (нет файлов, отключён, без Docker);
  * ``need-guard`` — не работает и авто-действие запрещено (чужой владелец);
  * ``fault``      — сбой нашего элемента (требуется вмешательство);
  * ``unknown``    — нет способа проверки.

Проверки дешёвые и безопасные: файл/каталог, HTTP ``GET``, TCP-connect,
наличие команды, наличие процесса. Внешних вызовов, меняющих состояние, нет.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import arsenal

STATES = ("active", "idle", "unloaded", "need-guard", "fault", "unknown")

# Состояния, требующие внимания оператора.
ATTENTION = ("fault", "need-guard")


def _probe_exists(probe: dict) -> tuple[bool, str]:
    target = str(probe.get("path") or "")
    if not target:
        return False, "путь не задан"
    path = Path(target)
    if path.exists():
        return True, "найден"
    return False, "не найден"


def _probe_http(probe: dict, timeout: float) -> tuple[bool, str]:
    url = str(probe.get("url") or "")
    if not url:
        return False, "url не задан"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - локальный адрес
            return 200 <= resp.status < 400, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # служба ответила (значит, жива), даже если вернула ошибку
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - недоступность не критична
        return False, f"недоступно ({type(exc).__name__})"


def _probe_tcp(probe: dict, timeout: float) -> tuple[bool, str]:
    host = str(probe.get("host") or "127.0.0.1")
    try:
        port = int(probe.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not port:
        return False, "порт не задан"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} открыт"
    except OSError:
        return False, f"{host}:{port} закрыт"


def _probe_command(probe: dict) -> tuple[bool, str]:
    command = probe.get("command") or []
    if isinstance(command, str):
        command = [command]
    if not command:
        return False, "команда не задана"
    binary = str(command[0])
    found = shutil.which(binary)
    if found:
        return True, f"команда найдена: {binary}"
    return False, f"команда не найдена: {binary}"


def _process_names() -> set[str]:
    """Имена процессов (best-effort, без внешних зависимостей)."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=5,
                                 check=False).stdout
            names = set()
            for line in out.splitlines():
                first = line.split(",")[0].strip().strip('"')
                if first:
                    names.add(first.lower())
            return names
        out = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True,
                             timeout=5, check=False).stdout
        return {line.strip().lower() for line in out.splitlines() if line.strip()}
    except Exception:  # noqa: BLE001 - список процессов не критичен
        return set()


def _probe_process(probe: dict) -> tuple[bool, str]:
    match = str(probe.get("match") or "").lower()
    if not match:
        return False, "имя процесса не задано"
    names = _process_names()
    hit = any(match in name for name in names)
    return hit, ("процесс найден" if hit else "процесс не запущен")


def run_probe(probe: dict | None, timeout: float = 1.5) -> tuple[bool, str]:
    """Исполнить проверку элемента. Возвращает (успех, пояснение)."""
    if not probe:
        return False, "нет способа проверки"
    kind = str(probe.get("kind") or "")
    if kind in ("file", "exists", "dir"):
        return _probe_exists(probe)
    if kind == "http":
        return _probe_http(probe, timeout)
    if kind == "tcp":
        return _probe_tcp(probe, timeout)
    if kind == "command":
        return _probe_command(probe)
    if kind == "process":
        return _probe_process(probe)
    return False, f"неизвестная проверка: {kind}"


def _owner(element: dict) -> str:
    owner = element.get("owner")
    if owner:
        return str(owner)
    return "factory" if element.get("class") in arsenal.FACTORY_OWNED else "external"


def state_for(element: dict, mode: str = "attached", *, timeout: float = 1.5) -> dict:
    """Определить состояние одного элемента арсенала."""
    owner = _owner(element)
    node = {
        "id": element.get("id"),
        "name": element.get("name"),
        "class": element.get("class"),
        "title": element.get("title"),
        "owner": owner,
        "source": element.get("source"),
        "tags": list(element.get("tags") or []),
        "available": bool(element.get("available")),
        "enabled": bool(element.get("enabled", True)),
        "path": element.get("path"),
        "docker": bool((element.get("meta") or {}).get("docker")),
        "meta": dict(element.get("meta") or {}),
    }

    if node["docker"]:
        node["state"] = "unloaded"
        node["detail"] = "исключено: проект без Docker"
        return node
    if not node["available"] or not node["enabled"]:
        node["state"] = "unloaded"
        node["detail"] = "не развёрнут или выключен"
        return node
    if not element.get("probe"):
        node["state"] = "unknown"
        node["detail"] = "нет способа проверки"
        return node

    ok, detail = run_probe(element.get("probe"), timeout=timeout)
    node["detail"] = detail
    if ok:
        node["state"] = "active"
    elif owner == "factory":
        node["state"] = "fault"
    elif str(mode) == "attached":
        node["state"] = "idle"
    else:
        node["state"] = "need-guard"
    return node


def snapshot(settings, data: dict | None = None, *, mode: str | None = None,
             timeout: float = 1.5, elements: list[dict] | None = None) -> dict:
    """Состояние всех элементов арсенала (сеть узлов)."""
    data = data if data is not None else arsenal.build(settings)
    resolved_mode = mode or str(
        (settings.get("operation.mode") if settings is not None else None) or "attached")
    items = elements if elements is not None else data.get("elements", [])
    nodes = [state_for(element, resolved_mode, timeout=timeout) for element in items]

    counts: dict[str, int] = {state: 0 for state in STATES}
    for node in nodes:
        counts[node["state"]] = counts.get(node["state"], 0) + 1
    summary = {
        "total": len(nodes),
        "by_state": counts,
        "working": counts.get("active", 0),
        "attention": sum(counts.get(state, 0) for state in ATTENTION),
        "mode": resolved_mode,
    }
    return {
        "schema": "invr.nodes/1",
        "mode": resolved_mode,
        "nodes": nodes,
        "summary": summary,
    }
