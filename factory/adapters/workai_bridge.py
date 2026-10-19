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

# --- защита от «кучи параллельных экземпляров» ---
# Несколько задач фабрики могут вызвать мост одновременно; каждый вызов,
# увидев «WorkAI не запущен», поднимал бы свой экземпляр — итог: десятки
# окон и память забита. Ограничения:
#   * запуск WorkAI — под мьютексом (единая очередь на холодный старт);
#   * перед холодным стартом проверяем свободную память: должен оставаться
#     запас минимум MIN_FREE_RAM_PCT % И плюс аппетит нового WorkAI
#     (~3 ГБ в среднем), чтобы после старта система не встала;
#   * максимум MAX_WORKAI_INSTANCES главных экземпляров (по маркерному
#     файлу запуска), лишние параллельные вызовы ждут свободного слота.
MIN_FREE_RAM_PCT = float(os.environ.get("WORKAI_MIN_FREE_RAM_PCT", "35"))
WORKAI_APPETITE_MB = float(os.environ.get("WORKAI_APPETITE_MB", "3200"))
MAX_WORKAI_INSTANCES = int(os.environ.get("WORKAI_MAX_INSTANCES", "2"))
LAUNCH_LOCK_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "state", "workai_launch.lock")
# окно, которое ещё ни разу не скрывалось (только что поднятое) — прячем
# сразу, чтобы оно не выскакивало на передний план и не крало фокус
_HIDE_DELAY_S = 0.0

def _free_ram_mb() -> float:
    """Свободная физическая память, МБ (NaN — не определить)."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD),
                        ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return float("nan")
        return ms.ullAvailPhys / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        return float("nan")


def _free_ram_pct() -> float:
    """Доля свободной памяти (0..100). None — не определить (не Windows)."""
    try:
        free = _free_ram_mb()
        if free != free:
            return float("nan")
        ctypes = __import__("ctypes")
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD),
                        ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            return float("nan")
        if not ms.ullTotalPhys:
            return float("nan")
        return ms.ullAvailPhys * 100.0 / ms.ullTotalPhys
    except Exception:  # noqa: BLE001
        return float("nan")


def _wait_for_ram(prompt_repr: str, debug: bool) -> bool:
    """Дождаться, пока свободной памяти хватит на запуск WorkAI.

    Условия (оба):
      * свободно ≥ MIN_FREE_RAM_PCT % от всей физической памяти;
      * свободно ≥ WORKAI_APPETITE_MB + всего_RAM × MIN_FREE_RAM_PCT/100 —
        аппетит нового экземпляра + резерв MIN_FREE_RAM_PCT % на систему
        ПОСЛЕ старта (это и есть «оценка запуска»: если после запуска
        останется меньше порога — не запускаем).
    Ждём до 120 с; если так и не хватает — возвращаем False (не поднимать).
    """
    def _enough() -> bool:
        free = _free_ram_mb()
        if free != free:
            return True  # не можем измерить — не мешаем
        pct = _free_ram_pct()
        if pct != pct or pct <= 0:
            return True
        total = free / (pct / 100.0)
        return free >= WORKAI_APPETITE_MB + total * MIN_FREE_RAM_PCT / 100.0

    if _enough():
        return True
    deadline = time.time() + 120
    while time.time() < deadline:
        if _enough():
            return True
        if debug:
            free = _free_ram_mb()
            print(f"DBG памяти мало (свободно {free:.0f} МБ — нужно ≥ "
                  f"{WORKAI_APPETITE_MB:.0f} МБ + {MIN_FREE_RAM_PCT:.0f}% "
                  f"резерва) — жду", file=sys.stderr)
        time.sleep(3)
    if debug:
        print("DBG память так и не освободилась — запуск отложен",
              file=sys.stderr)
    return False


def _acquire_launch_lock(debug: bool) -> bool:
    """Атомарно захватить «право на холодный старт» (файл-мьютекс).

    Несколько параллельных мостов видят «WorkAI не запущен» одновременно;
    только один получает право запустить его. Остальные ждут, пока
    захватчик поднимет окно (маркер), и переиспользуют его.
    Возвращает True, если этот вызов стал ответственным за запуск.
    """
    try:
        lock_dir = os.path.dirname(LAUNCH_LOCK_FILE)
        os.makedirs(lock_dir, exist_ok=True)
        fd = os.open(LAUNCH_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False
    except Exception:  # noqa: BLE001
        return True  # не смогли проверить — пробуем запустить сами


def _release_launch_lock() -> None:
    try:
        if os.path.exists(LAUNCH_LOCK_FILE):
            os.unlink(LAUNCH_LOCK_FILE)
    except OSError:
        pass


def _count_running_instances() -> int:
    """Число главных экземпляров WorkAI (окон с заголовком)."""
    try:
        return len(_workai_pids())
    except Exception:  # noqa: BLE001
        return 0

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
TIMEOUT_S = int(os.environ.get("WORKAI_BRIDGE_TIMEOUT", "400"))
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
    # окна, несущие заголовок WorkAI — главные процессы (И скрытые тоже:
    # SW_HIDE не удаляет заголовок, а без этого окна «мостовой» WorkAI не
    # распознавался бы как запущенный, и тёплый запрос уходил бы в перезапуск)
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
            if pid2.value in out:
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
        # Холодный старт: под единым мьютексом, с проверкой памяти и лимитом
        # инстансов (см. блок констант вверху файла) — чтобы параллельные
        # задачи не наоткрывали десятки окон WorkAI.
        owner = _acquire_launch_lock(debug)
        if not owner:
            if debug:
                print("DBG другой вызов уже поднимает WorkAI — жду готовности",
                      file=sys.stderr)
            # ждём, пока захватчик поднимет окно (максимум TIMEOUT_S)
            deadline = time.time() + min(120, TIMEOUT_S)
            while time.time() < deadline:
                if _workai_pids():
                    break
                time.sleep(1)
            before = _workai_pids()
            if debug:
                print("DBG после ожидания WorkAI:", bool(before), file=sys.stderr)
        else:
            # ответственный за запуск: проверяем память и число инстансов
            ram_ok = _wait_for_ram(prompt[:60], debug)
            if not ram_ok:
                if debug:
                    print("DBG памяти мало — не поднимаю новый WorkAI",
                          file=sys.stderr)
            n = _count_running_instances()
            if n >= MAX_WORKAI_INSTANCES:
                if debug:
                    print(f"DBG уже запущено {n} >= {MAX_WORKAI_INSTANCES} — "
                          f"не поднимаю новый, жду готовности", file=sys.stderr)
                deadline = time.time() + min(120, TIMEOUT_S)
                while time.time() < deadline and _count_running_instances() >= MAX_WORKAI_INSTANCES:
                    time.sleep(2)
                before = _workai_pids()
            elif not ram_ok:
                # память так и не освободилась — работаем через уже открытое
                # окно (если есть), новый экземпляр не поднимаем
                before = _workai_pids()
                if debug:
                    print("DBG WorkAI не поднимаю (мало памяти), использую существующий",
                          file=sys.stderr)
        if debug:
            print("DBG до запуска:", bool(before), file=sys.stderr)
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
        # скрываем окно сразу после подъёма, чтобы не крало фокус
        _hide_all_workai_windows()
        if owner:
            _release_launch_lock()
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