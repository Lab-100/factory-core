"""Один экземпляр интерфейса фабрики (single-instance).

Зачем. Клик по ярлыку десять раз не должен давать десять окон и десять
серверов дашборда. Первый запуск интерфейса (окно, веб-дашборд, трей) берёт
именованный мьютекс Windows; остальные видят «уже запущено», выводят
существующее окно на передний план и выходят с кодом 0.

Почему мьютекс, а не lock-файл. Мьютекс — объект ядра: если процесс упал или
его убили, блокировка снимается сама, «залипнуть» она не может. Lock-файл
после аварийного завершения способен остаться занятым и заблокировать
запуск навсегда. Мьютекс также атомарен: два процесса, стартовавшие
одновременно, не оба станут «первым».

Кто может разрешить несколько экземпляров. Оператор — флагом
``--allow-multiple`` или настройкой ``ui.allow_multiple``. По умолчанию
второй запуск интерфейса не создаёт ничего нового.

Имя мьютекса — ``Local\\FactoryLab100-<scope>-<пользователь>``: префикс
``Local`` держит объекты в сессии пользователя (не требует прав глобального
объекта), а имя пользователя разделяет запуски разных людей на одном
компьютере.

Использование::

    guard = InstanceGuard(root=root, title=tr("desktop.title"))
    if guard.acquire():                 # True — экземпляр уже есть
        print(owner, guard.focus())
        return 0
    try:
        serve()
    finally:
        guard.release()

Вне Windows используется блокировка файла в ``state/`` (msvcrt/fcntl).
"""
from __future__ import annotations

import errno
import json
import os
import sys
import time
from pathlib import Path

#: Код ``GetLastError`` при попытке создать уже существующий мьютекс.
ERROR_ALREADY_EXISTS = 183

#: Результаты ``WaitForSingleObject(handle, 0)`` для мьютекса.
WAIT_OBJECT_0 = 0x000      # захватили
WAIT_ABANDONED = 0x080     # прежний владелец умер — забираем
WAIT_TIMEOUT = 0x102       # держит другой процесс

#: Область (scope) мьютекса по умолчанию: весь интерактивный интерфейс.
DEFAULT_SCOPE = "ui"

#: Имя файла с метаданными владельца экземпляра.
STATE_FILE = "ui-instance.json"

#: Заголовки окон фабрики на разных языках — для поиска существующего окна.
WINDOW_TITLES = ("Фабрика Lab-100", "Factory Lab-100", "工厂 Lab-100")


def mutex_name(scope: str = DEFAULT_SCOPE, user: str | None = None) -> str:
    """Имя именованного мьютекса: сессия, приложение, область, пользователь."""
    who = user or os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return f"Local\\FactoryLab100-{scope}-{who}"


def state_path(root=None, scope: str = DEFAULT_SCOPE) -> Path:
    """Путь к файлу метаданных экземпляра (``<корень>/state/ui-instance.json``)."""
    if root is None:
        from .paths import repo_root

        root = repo_root()
    return Path(root) / "state" / STATE_FILE


def _read_owner(path: Path) -> dict:
    """Прочитать метаданные владельца; при неудаче — пустой словарь."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_owner(path: Path, data: dict) -> None:
    """Атомарно записать метаданные: временный файл + подмена (без гонок)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass


def _drop_owner(path: Path, pid: int) -> None:
    """Убрать метаданные, только если владельцем был этот процесс."""
    data = _read_owner(path)
    if not data or int(data.get("pid", -1)) == int(pid):
        try:
            Path(path).unlink()
        except OSError:
            pass


def is_alive(pid: int) -> bool:
    """Жив ли процесс с таким PID (безопасная проверка для Windows)."""
    if not pid or int(pid) <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        except Exception:  # noqa: BLE001 — проверка не должна ломать запуск
            return False
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass
    try:
        os.kill(int(pid), 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def focus_window(titles=WINDOW_TITLES) -> int | None:
    """Вывести на передний план окно фабрики; вернуть его PID или None.

    Ищет обычное окно с заголовком фабрики (язык интерфейса может быть
    ru/en/zh — перебираем все варианты), восстанавливает из свёрнутого и
    поднимает наверх.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        window = None
        for title in titles:
            window = user32.FindWindowW(None, str(title))
            if window:
                break
        if not window:
            return None
        user32.ShowWindow(window, 9)              # SW_RESTORE
        user32.BringWindowToTop(window)
        user32.SetForegroundWindow(window)
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:  # noqa: BLE001 — активация окна не должна ломать запуск
        return None


def record_instance(url: str = "", root=None, scope: str = DEFAULT_SCOPE) -> None:
    """Дописать адрес дашборда в метаданные текущего экземпляра.

    Вызывает интерфейс, как только поднял сервер: адрес нужен, чтобы повторный
    запуск мог открыть именно работающий дашборд, а не искать окно.
    """
    path = state_path(root, scope)
    data = _read_owner(path)
    if not data or int(data.get("pid", 0) or 0) != os.getpid():
        return
    data["url"] = url
    _write_owner(path, data)


def open_url(url: str, new: int = 0) -> bool:
    """Открыть адрес в браузере пользователя (запасной путь для повторного запуска)."""
    if not url:
        return False
    try:
        import webbrowser

        return bool(webbrowser.open(url, new=new))
    except Exception:  # noqa: BLE001
        return False


#: Порты отладочного протокола браузера (CDP), проверяемые при повторном запуске.
CDP_PORTS = (9222, 9223, 9229)


def cdp_ports() -> tuple[int, ...]:
    """Порты CDP: сперва из ``FACTORY_CDP_PORTS``, затем стандартные."""
    raw = os.environ.get("FACTORY_CDP_PORTS", "")
    extra: list[int] = []
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if token.isdigit():
            extra.append(int(token))
    return tuple(dict.fromkeys([*extra, *CDP_PORTS]))


def reuse_browser_tab(url: str, ports: tuple[int, ...] | None = None) -> bool:
    """Поднять на передний план уже открытую вкладку дашборда (через CDP).

    Новая вкладка при этом не создаётся: повторный запуск не плодит страницы,
    а показывает ту, что уже открыта и сама обновляет содержимое. Работает,
    если браузер запущен с ключом ``--remote-debugging-port=<порт>``; иначе
    возвращает ``False``, и вызывающий сам решает, открывать ли страницу.
    """
    if not url:
        return False
    import urllib.request

    target = url.rstrip("/")
    for port in (ports if ports is not None else cdp_ports()):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=1.5) as resp:
                pages = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — порт закрыт или это не CDP
            continue
        for page in pages or []:
            if str(page.get("type") or "") != "page":
                continue
            if str(page.get("url") or "").rstrip("/") != target:
                continue
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/activate/{page.get('id')}",
                        timeout=1.5):
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def open_dashboard(url: str, prefer_reuse: bool = True) -> str:
    """Показать дашборд.

    Возвращает ``reused`` — активировали открытую вкладку, ``opened`` —
    открыли новую страницу, ``none`` — не удалось ни то, ни другое.
    """
    if prefer_reuse and reuse_browser_tab(url):
        return "reused"
    if open_url(url):
        return "opened"
    return "none"


class _Mutex:
    """Именованный мьютекс Windows; при недоступности API — «мягкая» заглушка."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.handle = None
        self.taken = False
        self.degraded = False

    def acquire(self) -> bool:
        """Занять мьютекс. True — его держит другой процесс.

        Занятость определяется не только ``ERROR_ALREADY_EXISTS`` (объект мог
        остаться существовать свободным), а попыткой захвата:
        ``WaitForSingleObject(handle, 0)`` даёт ``WAIT_OBJECT_0`` — захватили,
        ``WAIT_ABANDONED`` — прежний владелец умер (забираем), ``WAIT_TIMEOUT``
        — держит другой процесс (второй запуск отменяем).
        """
        if sys.platform != "win32":
            self.degraded = True
            return False
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            k32.WaitForSingleObject.restype = wintypes.DWORD
            self.handle = k32.CreateMutexW(None, False, self.name)
            if not self.handle:
                self.degraded = True
                return False
            wait = int(k32.WaitForSingleObject(self.handle, 0))
            if wait == WAIT_TIMEOUT:
                self.taken = True
                return True
            if wait in (WAIT_OBJECT_0, WAIT_ABANDONED):
                self.taken = True      # WAIT_ABANDONED: прежний владелец умер
                return False
            self.taken = True          # неожиданный код — не блокируем запуск
            self.degraded = True
            return False
        except Exception:  # noqa: BLE001 — не блокируем запуск из-за мьютекса
            self.degraded = True
            self.handle = None
            return False

    def release(self) -> None:
        """Отпустить мьютекс и закрыть дескриптор."""
        if not self.handle:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.handle = None
            self.taken = False


class _FileLock:
    """Запасной вариант вне Windows: блокировка файла в ``state/``."""

    def __init__(self, name: str, root=None) -> None:
        self.name = name
        self.path = state_path(root).with_suffix(".lock")
        self.handle = None
        self.degraded = False

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+")
        except OSError:
            self.degraded = True
            return False
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except Exception:  # noqa: BLE001 — файл занят другим процессом
            return True

    def release(self) -> None:
        if not self.handle:
            return
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.handle.close()
        except OSError:
            pass
        self.handle = None


class InstanceGuard:
    """Охрана «только один экземпляр интерфейса».

    ``acquire()`` возвращает ``True``, когда экземпляр уже есть и повторный
    запуск надо отменить. ``allow_multiple`` (флаг оператора или настройка)
    снимает ограничение. ``focus()`` выводит существующее окно на передний
    план, ``owner()`` — метаданные текущего владельца (pid, режим, порт).
    """

    def __init__(self, root=None, scope: str = DEFAULT_SCOPE,
                 allow_multiple: bool = False, mode: str = "") -> None:
        self.root = Path(root) if root is not None else None
        self.scope = scope
        self.allow_multiple = bool(allow_multiple)
        self.mode = mode or ""
        self.name = mutex_name(scope)
        self.path = state_path(self.root, scope)
        self._backend = (_Mutex(self.name) if sys.platform == "win32"
                         else _FileLock(self.name, self.root))
        self._held = False

    # --- состояние ---
    @property
    def held(self) -> bool:
        """Держит ли этот процесс экземпляр."""
        return self._held

    @property
    def degraded(self) -> bool:
        """Не удалось применить блокировку (запуск не блокируется)."""
        return bool(getattr(self._backend, "degraded", False))

    def owner(self) -> dict:
        """Метаданные процесса, который сейчас держит экземпляр."""
        data = _read_owner(self.path)
        if not data:
            return {}
        pid = int(data.get("pid", 0) or 0)
        if pid and pid != os.getpid() and not is_alive(pid):
            return {}          # владелец умер — запись протухшая
        return data

    # --- захват ---
    def acquire(self) -> bool:
        """Взять экземпляр. True — экземпляр уже занят, повтор отменяется."""
        if self.allow_multiple:
            return False
        taken = bool(self._backend.acquire())
        if taken:
            return True
        self._held = True
        _write_owner(self.path, {
            "pid": os.getpid(),
            "scope": self.scope,
            "mode": self.mode,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return False

    def release(self) -> None:
        """Отпустить экземпляр (вызывать в ``finally``)."""
        if not self._held:
            return
        _drop_owner(self.path, os.getpid())
        self._backend.release()
        self._held = False

    def focus(self) -> int | None:
        """Вывести окно существующего экземпляра на передний план."""
        return focus_window()

    # --- контекстный менеджер ---
    def __enter__(self) -> "InstanceGuard":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def describe_owner(guard: InstanceGuard) -> dict:
    """Сведения о владельце для сообщения пользователю."""
    owner = guard.owner()
    return {
        "pid": owner.get("pid", "—"),
        "mode": owner.get("mode", ""),
        "started": owner.get("started", ""),
    }