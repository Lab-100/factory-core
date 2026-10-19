#!/usr/bin/env python3
"""Мост factory external_agent -> WorkAI.

Фабрика (external_agent) запускает команду провайдера и передаёт промпт в stdin,
ожидая ответ в stdout. Графический `workai chat` ответ в stdout не отдаёт — он
сохраняется в сессию чата (workspaceStorage/*/chatSessions/*.jsonl).

Этот мост (в отличие от старого, лежавшего вне репозитория):
  1) отправляет промпт НАТИВНЫМ stdin-режимом CLI: `workai chat <prompt> -`.
     WorkAI сам пишет промпт во временный файл и доставляет его в сессию
     (внутренний механизм Electron-приложения), без мыши и клавиатуры —
     поэтому вопрос доходит стабильно, а не "иногда".
  2) после получения ответа (или таймаута) ЗАКРЫВАЕТ только то окно WorkAI,
     которое сам и поднял. Если WorkAI уже был открыт пользователем —
     мост его не трогает и сворачивает обратно как было.

Подробности формата сессии:
  JSONL; события kind:2 с k==["requests"] содержат список запросов:
    v: [ {requestId, timestamp, message:{text}, response:[{kind,value,...}], ...} ]
  Текст ответа модели — в response[] у части с kind=="content" (поле value).
  Мысли модели — kind=="thinking" (тоже value).

Таймаут — жёсткий (WORKAI_BRIDGE_TIMEOUT, по умолчанию 60 с), чтобы фабрика
не ждала бесконечно.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time

WORKAI = r"C:\Program Files\WorkAI\bin\workai.cmd"
WORKAI_EXE = r"C:\Program Files\WorkAI\WorkAI.exe"
WORKSPACE_STORAGE = os.path.join(
    os.environ.get("APPDATA", ""), "WorkAI", "User", "workspaceStorage"
)
TIMEOUT_S = int(os.environ.get("WORKAI_BRIDGE_TIMEOUT", "60"))
POLL_S = 0.5
LAUNCH_MARGIN_MS = 5000  # допуск на рассинхрон часов при сравнении timestamp


def read_prompt() -> str:
    """Промпт из stdin (передан фабрикой); аргументы-флаги игнорируем."""
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data and data.strip():
                return data.strip()
    except Exception:  # noqa: BLE001 - stdin может быть недоступен
        pass
    args = [
        a for a in sys.argv[1:]
        if not a.startswith("-") and a not in ("chat", "ask", "agent")
    ]
    return " ".join(args).strip()


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def iter_session_files() -> list[str]:
    patterns = [
        os.path.join(WORKSPACE_STORAGE, "*", "chatSessions", "*.jsonl"),
        os.path.join(os.path.dirname(WORKSPACE_STORAGE), "globalStorage",
                     "emptyWindowChatSessions", "*.jsonl"),
    ]
    files: list[str] = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    return sorted(files, key=os.path.getmtime, reverse=True)


def extract_response(path: str, prompt: str, min_ts_ms: int) -> str | None:
    """Ответ на промпт из файла сессии.

    Работает с двумя вариантами хранения (проверено на WorkAI):
      kind:0  -> v.requests: [ {message, response[], timestamp}, ... ] (снимок)
      kind:2  -> k==["requests"]: v — то же самое (событие записи)
      kind:2  -> k==["requests", <N>, "response"]: v — массив частей ответа
    Сопоставление по нормализованному тексту запроса (точно) и ts >= min_ts_ms.
    """
    norm_prompt = _norm(prompt)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    ordered: list[tuple[int, dict]] = []
    overrides: dict[int, list] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, k = obj.get("kind"), obj.get("k")
        v = obj.get("v")
        if kind == 0 and isinstance(v, dict):
            for idx, req in enumerate(v.get("requests") or []):
                if isinstance(req, dict) and req.get("requestId"):
                    ordered.append((idx, dict(req)))
        elif kind == 2 and k == ["requests"] and isinstance(v, list):
            for idx, req in enumerate(v):
                if isinstance(req, dict) and req.get("requestId"):
                    ordered.append((idx, dict(req)))
        elif kind == 2 and isinstance(k, list) and len(k) == 3 \
                and k[0] == "requests" and k[2] == "response" and isinstance(v, list):
            try:
                idx = int(k[1])
            except (TypeError, ValueError):
                continue
            overrides[idx] = v
    for idx, req in ordered:
        msg = ((req.get("message") or {}).get("text")) or ""
        if _norm(msg) != norm_prompt:
            continue
        ts = int(req.get("timestamp") or 0)
        if ts and ts < min_ts_ms:
            continue
        parts = overrides.get(idx) or req.get("response") or []
        text = ""
        for part in reversed(parts):
            if not isinstance(part, dict):
                continue
            if part.get("kind") == "thinking":
                continue
            val = part.get("value")
            if isinstance(val, str) and val.strip():
                text = val.strip()
                break
        if text:
            return text
    return None


# --- управление окном WorkAI ---

def _workai_pids() -> set[int]:
    """PID главных процессов WorkAI.exe (без дочерних Electron-веток)."""
    try:
        import psutil
        out: set[int] = set()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = proc.info.get("name") or ""
                cmdline = proc.info.get("cmdline") or []
                if name.lower() == "workai.exe" and not any(
                        c.startswith("--type=") for c in cmdline):
                    out.add(proc.info["pid"])
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return set()


def _workai_orphans() -> set[int]:
    """PID главных процессов WorkAI.exe, оставшихся без живого родителя.

    Мост запускает WorkAI как дочерний процесс (родитель — python-мост). Когда
    мост завершается, а окно остаётся — родитель уже мёртв, и такое окно —
    осиротевший мусор от моста. Пользовательское окно WorkAI (запущенное из
    ярлыка/меню) имеет живого родителя (explorer) и не трогается.
    """
    try:
        import psutil
        out: set[int] = set()
        for proc in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
            try:
                name = proc.info.get("name") or ""
                cmdline = proc.info.get("cmdline") or []
                if name.lower() != "workai.exe" or any(
                        c.startswith("--type=") for c in cmdline):
                    continue
                ppid = int(proc.info.get("ppid") or 0)
                parent_alive = False
                if ppid > 0:
                    try:
                        parent = psutil.Process(ppid)
                        parent_alive = parent.is_running() and parent.status() != "zombie"
                    except Exception:  # noqa: BLE001
                        parent_alive = False
                if not parent_alive:
                    out.add(proc.info["pid"])
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return set()


def _kill_tree(pid: int) -> None:
    """Принудительно завершить процесс и его потомков."""
    try:
        import psutil
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:  # noqa: BLE001
                pass
        parent.kill()
    except Exception:  # noqa: BLE001
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass


def ensure_workai_running() -> bool:
    """WorkAI запущен? (главное окно существует)."""
    return bool(_workai_pids())


def launch_workai_native(prompt: str) -> int | None:
    """Запустить `workai chat <prompt> -` — нативный stdin-режим CLI.

    WorkAI сам пишет промпт во временный файл и доставляет в сессию окна.
    Возвращает PID процесса CLI (не окна WorkAI).
    """
    try:
        proc = subprocess.Popen(
            [WORKAI, "chat", prompt, "-", "-m", "ask", "-r"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.pid
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: не удалось запустить workai chat: {exc}", file=sys.stderr)
        return None


def main() -> int:
    prompt = read_prompt()
    if not prompt:
        print("ERROR: пустой промпт (stdin пуст)", file=sys.stderr)
        return 1
    debug = os.environ.get("WORKAI_BRIDGE_DEBUG") == "1"
    if debug:
        print(f"DBG prompt[{len(prompt)}]={prompt[:80]!r}", file=sys.stderr)

    before = _workai_pids()
    if not before:
        try:
            subprocess.Popen([WORKAI_EXE],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # ждём, пока графическое окно WorkAI полностью поднимется —
            # иначе `workai chat -` не успевает доставить промпт в сессию
            ready = time.time() + 40
            while time.time() < ready:
                if _workai_pids():
                    # окно появилось; даём Electron ещё довести сессии до готовности
                    time.sleep(3)
                    break
                time.sleep(1)
            if debug:
                print("DBG WorkAI поднят:", bool(_workai_pids()), file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: не удалось поднять фоновый WorkAI: {exc}", file=sys.stderr)
    launch_ms = int(time.time() * 1000)
    if debug:
        print("DBG workai chat (stdin) запускается", file=sys.stderr)
    pid = launch_workai_native(prompt)
    if debug:
        print(f"DBG workai chat pid={pid}", file=sys.stderr)

    min_ts_ms = launch_ms - LAUNCH_MARGIN_MS
    deadline = time.time() + TIMEOUT_S
    attempts = 0
    answer = None
    while time.time() < deadline:
        attempts += 1
        for path in iter_session_files():
            text = extract_response(path, prompt, min_ts_ms)
            if text:
                answer = text
                break
        if answer:
            break
        time.sleep(POLL_S)

    if debug:
        print(f"DBG попыток {attempts}, файлов сессий {len(iter_session_files())}",
              file=sys.stderr)

    # Стратегия: НЕ закрываем WorkAI, поднятый мостом (даже холодный старт).
    # WorkAI остаётся работать как служебное окно — тогда каждый следующий
    # запрос доставляется в уже готовое окно стабильно и быстро. Закрытие окна
    # на каждом ответе ведёт к холодным стартам и потерянным промптам.
    if debug:
        print("DBG WorkAI оставлен открытым для последующих запросов", file=sys.stderr)

    if answer:
        sys.stdout.write(answer)
        sys.stdout.flush()
        return 0
    print(
        f"ERROR: таймаут {TIMEOUT_S} с — ответ WorkAI на промпт не найден в сессиях",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())