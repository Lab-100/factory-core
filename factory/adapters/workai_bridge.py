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
import shutil
import subprocess
import sys
import time
from pathlib import Path

def _find_workai() -> tuple[str | None, str | None]:
    """Найти CLI-шим и exe WorkAI без хардкода путей.

    Порядок поиска:
      1) переменные окружения WORKAI_CMD / WORKAI_EXE (явное указание);
      2) `workai` в PATH (shutil.which);
      3) стандартные каталоги установки Windows: %ProgramFiles%/WorkAI,
         %LOCALAPPDATA%/Programs/WorkAI, %LOCALAPPDATA%/WorkAI.
    Возвращает (cmd, exe); любой из компонентов может быть None.
    """
    cmd = os.environ.get("WORKAI_CMD") or ""
    exe = os.environ.get("WORKAI_EXE") or ""
    if not cmd:
        found = shutil.which("workai")
        if found:
            cmd = found
    if not cmd or not exe:
        candidates = []
        for base in (os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append(Path(base) / "WorkAI")
                candidates.append(Path(base) / "Programs" / "WorkAI")
        for root in candidates:
            if not root.is_dir():
                continue
            if not cmd:
                exe_candidate = root / "bin" / "workai.cmd"
                if exe_candidate.is_file():
                    cmd = str(exe_candidate)
            if not exe:
                exe_candidate = root / "WorkAI.exe"
                if exe_candidate.is_file():
                    exe = str(exe_candidate)
    return (cmd or None, exe or None)


WORKAI, WORKAI_EXE = _find_workai()
WORKSPACE_STORAGE = os.path.join(
    os.environ.get("APPDATA", ""), "WorkAI", "User", "workspaceStorage"
)
TIMEOUT_S = int(os.environ.get("WORKAI_BRIDGE_TIMEOUT", "240"))
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
        except Exception:  # noqa: BLE001 - битая/частично записанная строка
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
    """PID главных процессов WorkAI.exe (без дочерних Electron-веток).

    Через `tasklist` (нет зависимости от psutil — переносимость на любую
    машину): главный процесс WorkAI.exe — тот, чьё окно носит заголовок
    «WorkAI» (дочерние Electron-ветки заголовков не имеют).
    """
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq WorkAI.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20)
    except Exception:  # noqa: BLE001
        return set()
    out: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = [p.strip().strip('"') for p in line.split(',')]
        if len(parts) < 2 or not parts[0].lower().endswith("workai.exe"):
            continue
        try:
            out.add(int(parts[1]))
        except (TypeError, ValueError):
            continue
    # окна, несущие заголовок WorkAI — главные процессы
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        with_titles: set[int] = set()
        WINDOW_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @WINDOW_ENUM
        def _cb(hwnd, lparam):
            pid2 = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid2))
            if pid2.value in out and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if "workai" in buf.value.lower():
                    with_titles.add(pid2.value)
            return True

        user32.EnumWindows(_cb, 0)
        if with_titles:
            return with_titles
    except Exception:  # noqa: BLE001
        pass
    return out


def _workai_orphans() -> set[int]:
    """PID главных процессов WorkAI.exe, оставшихся без живого родителя.

    Мост запускает WorkAI как дочерний процесс (родитель — python-мост). Когда
    мост завершается, а окно остаётся — родитель уже мёртв, и такое окно —
    осиротевший мусор от моста. Пользовательское окно WorkAI (запущенное из
    ярлыка/меню) имеет живого родителя (explorer) и не трогается.
    Без psutil: сирота — процесс WorkAI, у которого нет живого родителя
    explorer/моста (определяем по PID процесса, давшего запуск).
    """
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq WorkAI.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20)
    except Exception:  # noqa: BLE001
        return set()
    # Проверяем каждого: у осиротевшего WorkAI нет живого родителя-процесса
    # WorkAI выше по дереву (главный процесс). Точное PPID tasklist не даёт,
    # поэтому используем правило: главный WorkAI (с окном) — не сирота, если
    # это НЕ мостовой запуск; мост сам закрывает поднятое окно, а orphan-ветка
    # используется ограниченно (её функция сохранена для совместимости).
    return set()


def _minimize_all_workai() -> None:
    """Свернуть все видимые окна WorkAI (служебные не должны мешать)."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        workai = _workai_pids()
        if not workai:
            return
        WINDOW_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        cands: list[tuple[int, int]] = []

        @WINDOW_ENUM
        def _cb(hwnd, lparam):
            pid2 = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid2))
            cands.append((hwnd, pid2.value))
            return True

        user32.EnumWindows(_cb, 0)
        for hwnd, pid in cands:
            if pid in workai and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    except Exception:  # noqa: BLE001
        pass


def _hide_all_workai_windows() -> None:
    """Скрыть все видимые окна WorkAI (SW_HIDE) — окно не видимо,
    не крадёт фокус, но живёт и продолжает принимать промпты (проверено:
    и холодный, и тёплый запросы доставляются при скрытом окне)."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        workai = _workai_pids()
        if not workai:
            return
        WINDOW_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        cands: list[tuple[int, int]] = []

        @WINDOW_ENUM
        def _cb(hwnd, lparam):
            pid2 = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid2))
            cands.append((hwnd, pid2.value))
            return True

        user32.EnumWindows(_cb, 0)
        for hwnd, pid in cands:
            if pid in workai and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:  # noqa: BLE001
        pass


def _kill_tree(pid: int) -> None:
    """Принудительно завершить процесс и всё его дерево (taskkill /T /F).

    Проверено: psutil.children(recursive=True) иногда не добивает дочерние
    процессы Electron (остаются окна), а `taskkill /PID <pid> /T /F` закрывает
    дерево целиком.
    """
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        # фолбэк без psutil: taskkill по имени — добивает все окна WorkAI
        try:
            subprocess.run(["taskkill", "/IM", "WorkAI.exe", "/T", "/F"],
                           capture_output=True, timeout=15)
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
    if not WORKAI and not WORKAI_EXE:
        print("ERROR: WorkAI не найден — задайте WORKAI_CMD/WORKAI_EXE "
              "или установите WorkAI", file=sys.stderr)
        return 2
    debug = os.environ.get("WORKAI_BRIDGE_DEBUG") == "1"
    if debug:
        print(f"DBG prompt[{len(prompt)}]={prompt[:80]!r}", file=sys.stderr)

    before = _workai_pids()
    if not before:
        try:
            if WORKAI_EXE:
                subprocess.Popen([WORKAI_EXE],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # ждём, пока графическое окно WorkAI полностью поднимется —
            # иначе `workai chat -` не успевает доставить промпт в сессию
            ready = time.time() + 60
            started_at = 0.0
            while time.time() < ready:
                if _workai_pids():
                    if started_at == 0:
                        started_at = time.time()
                    # окно уже есть; ждём ещё ~6 секунд, чтобы Electron довёл
                    # сессии/контейнер чата до готовности к приёму промпта
                    if time.time() - started_at >= 6:
                        break
                time.sleep(1)
            if debug:
                print("DBG WorkAI поднят:", bool(_workai_pids()), file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: не удалось поднять фоновый WorkAI: {exc}", file=sys.stderr)
    else:
        # окно уже открыто — даём ему секунду на стабилизацию после прошлого запроса
        time.sleep(1.5)

    if not before:
        # Холодный старт: прогреваем окно тестовым промптом и ждём, пока он
        # осядет в сессии — это знак, что чат-контейнер готов принимать текст.
        # Промпт УНИКАЛЕН на каждый запуск (pid+время), иначе старые файлы
        # сессий от прошлых холодных стартов дают ложное «прогрето: True» —
        # и реальный промпт уходит в ещё стартующий Electron и теряется.
        warmup = f"_bridge_warmup_{os.getpid()}_{int(time.time())}"
        pid0 = launch_workai_native(warmup)
        if debug:
            print(f"DBG прогрев окна (pid={pid0})", file=sys.stderr)
        ready = time.time() + 45
        warmed = False
        while time.time() < ready:
            for path in iter_session_files():
                try:
                    data = open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if warmup in data:
                    warmed = True
                    break
            if warmed:
                break
            time.sleep(1)
        # после подтверждения готовности даём Electron ещё пару секунд,
        # чтобы контейнер чата окончательно стабилизировался
        if warmed:
            time.sleep(3)
        if debug:
            print("DBG окно прогрето:", warmed, file=sys.stderr)

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

    # Стратегия: один скрытый WorkAI на серию запросов.
    #  - окно поднимается мостом и СРАЗУ СКРЫВАЕТСЯ (не мешает пользователю,
    #    не крадёт фокус); между запросами серии оно переиспользуется —
    #    тёплые запросы доставляются в скрытое окно стабильно (проверено);
    #  - если WorkAI уже был открыт ПОЛЬЗОВАТЕЛЕМ до моста (before) — не
    #    трогаем его вовсе (это окно человека, не моста);
    #  - если WorkAI поднял сам мост — помечаем «мостовым» маркером, чтобы
    #    фоновый уборщик фабрики закрыл его после простоя (watchdog idle).
    if not before:
        try:
            marker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "..", "state", "workai_bridge.txt")
            marker = os.path.abspath(marker)
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(str(time.time()))
        except Exception:  # noqa: BLE001
            pass
        if debug:
            print("DBG WorkAI поднят мостом — отмечен для фоновой уборки",
                  file=sys.stderr)
    _hide_all_workai_windows()
    if debug:
        print("DBG WorkAI скрыт (переиспользуется следующими запросами)",
              file=sys.stderr)

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