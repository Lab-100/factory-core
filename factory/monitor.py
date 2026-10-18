"""Микромонитор запуска: перехват вывода, ошибок и кода возврата фабрики.

Зачем нужен. Когда приложение сообщает об ошибке и мгновенно закрывается,
текст теряется: окно консоли мигает и исчезает. Микромонитор запускает
команду сам, построчно ловит ``stdout``/``stderr`` вместе с метками времени,
пишет всё в журнал и собирает ошибки «столбиком» — дата, время, источник
(поток или файл) и текст ошибки.

Что считается ошибкой:

* любая строка ``stderr``;
* строка ``stdout`` с маркерами сбоя (``Traceback``, ``Error``, ``Ошибка``,
  ``не удалось``, ``недоступен``, ``No module named`` и подобные);
* ненулевой код возврата процесса (добавляется отдельной записью).

Использование из кода::

    monitor = LaunchMonitor([...команда...])
    report = monitor.run()
    print(report["report"])          # ошибки столбиком
    report["ok"]                     # True, если запуск прошёл без ошибок

Из командной строки: ``python -m factory monitor``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

STAMP = "%d.%m.%Y %H:%M:%S"

#: Маркеры сбоя в строке вывода (сравнение в нижнем регистре).
ERROR_MARKERS: tuple[str, ...] = (
    "traceback",
    "exception",
    "syntaxerror",
    "importerror",
    "modulenotfounderror",
    "no module named",
    "error:",
    "error ",
    "error,",
    "error.",
    "ошибка",
    "ошибк",
    "сбой",
    "не удалось",
    "не найден",
    "недоступен",
    "не доступен",
    "отказано",
    "не запускается",
    "не запустилась",
    "failed",
    "failure",
    "fatal",
    "critical",
    "panic",
    "not found",
)

#: Строки, которые похожи на ошибку, но ею не являются.
FALSE_MARKERS: tuple[str, ...] = (
    "0 errors",
    "errors=0",
    "errors: 0",
    "без ошибок",
    "no errors",
    "error=0",
)

#: Путь к файлу внутри строки вывода (для колонки «источник»).
_PATH_RE = None  # заполняется ниже, чтобы не тянуть re в аннотации


def _path_pattern():
    import re

    return re.compile(
        r"""(
            [A-Za-z]:\\[^:*?"<>|\r\n]+\.[A-Za-z0-9]{1,6}   # windows-путь с расширением
          | [^\s:;|,]+\.(?:py|pyw|ps1|cmd|bat|exe|toml|json|jsonl|log|md|ini|cfg|txt)
        )""",
        re.VERBOSE | re.IGNORECASE,
    )


_PATH_RE = None


def _path_pattern_compiled():
    """Скомпилированное регулярное выражение для поиска пути в строке."""
    global _PATH_RE
    if _PATH_RE is None:
        _PATH_RE = _path_pattern()
    return _PATH_RE


def _now() -> str:
    """Текущая дата и время в формате «ДД.ММ.ГГГГ ЧЧ:ММ:СС»."""
    return datetime.now().strftime(STAMP)


def looks_like_error(text: str) -> bool:
    """Считать ли строку вывода ошибкой (эвристика по маркерам)."""
    low = (text or "").strip().lower()
    if not low:
        return False
    if any(marker in low for marker in FALSE_MARKERS):
        return False
    return any(marker in low for marker in ERROR_MARKERS)


def extract_source(text: str, stream: str, fallback: str = "") -> str:
    """Источник строки: имя файла из текста, иначе имя потока."""
    match = _path_pattern_compiled().search(text or "")
    if match:
        return match.group(1).strip().strip("\"'")
    return fallback or stream


def _base_command() -> list[str]:
    """Как запускать фабрику: собранный exe, если это сборка, иначе исходники."""
    if getattr(sys, "frozen", False):  # PyInstaller-сборка
        return [sys.executable]
    runner = sys.executable or "python"
    return [runner, "-m", "factory"]


def default_target() -> list[str]:
    """Команда по умолчанию — фабрика в оконном режиме (как ярлык «окно»)."""
    return _base_command() + ["ui", "--mode", "desktop"]


class LaunchMonitor:
    """Запуск команды с перехватом вывода, журналом и списком ошибок.

    ``command`` — список аргументов запуска; ``cwd`` — рабочий каталог;
    ``log_path``/``errors_path`` — журналы (по умолчанию в ``<корень>/state``).
    """

    def __init__(self, command: list[str] | None = None, cwd: str | None = None,
                 log_path: str | Path | None = None,
                 errors_path: str | Path | None = None,
                 root: str | Path | None = None) -> None:
        from .paths import repo_root

        self.root = Path(root) if root is not None else repo_root()
        self.command = [str(item) for item in (command or default_target())]
        self.cwd = str(cwd or self.root)
        state = self.root / "state"
        self.log_path = Path(log_path) if log_path else state / "monitor.log"
        self.errors_path = Path(errors_path) if errors_path else state / "monitor-errors.log"
        self.entries: list[dict] = []
        self.returncode: int | None = None

    # --- сбор строк ---
    def _add(self, stream: str, text: str, process_name: str = "") -> None:
        text = (text or "").rstrip("\r\n")
        if not text:
            return
        error = stream == "stderr" or looks_like_error(text)
        self.entries.append({
            "at": _now(),
            "stream": stream,
            "source": extract_source(text, stream, process_name),
            "text": text,
            "error": bool(error),
        })

    @staticmethod
    def _format_entry(entry: dict) -> str:
        """Одна строка «столбиком»: [дата время] источник: текст."""
        return f"[{entry['at']}] {entry['source']}: {entry['text']}"

    def to_text(self, entries: list[dict] | None = None) -> str:
        """Отчёт текстом: строки ошибок столбиком."""
        rows = self.entries if entries is None else entries
        return "\n".join(self._format_entry(entry) for entry in rows)

    @property
    def errors(self) -> list[dict]:
        """Только записи, отмеченные как ошибки."""
        return [entry for entry in self.entries if entry.get("error")]

    # --- журналы ---
    def _write(self, path: Path, lines: list[str]) -> None:
        if not lines:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def save(self) -> dict:
        """Записать накопленное в журнал (весь вывод) и журнал ошибок."""
        name = Path(self.command[0]).name if self.command else "?"
        self._write(self.log_path, [
            f"[{entry['at']}] [{entry['stream']}] {name}: {entry['text']}"
            for entry in self.entries
        ])
        self._write(self.errors_path, [self._format_entry(e) for e in self.errors])
        return {"log": str(self.log_path), "errors_log": str(self.errors_path)}

    # --- запуск ---
    def run(self, timeout: float | None = None) -> dict:
        """Запустить команду и вернуть отчёт. Исключения наружу не летят."""
        started = time.monotonic()
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.Popen(
                self.command, cwd=self.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=env,
            )
        except FileNotFoundError as exc:
            self._add("stderr", f"команда не найдена: {self.command[0] if self.command else '?'} ({exc})")
            return self._report(started)
        except OSError as exc:  # noqa: BLE001 — сбой запуска обязан попасть в отчёт
            self._add("stderr", f"{type(exc).__name__}: {exc}")
            return self._report(started)

        def pump(stream_name: str, pipe) -> None:
            try:
                for line in pipe:
                    self._add(stream_name, line)
            finally:
                try:
                    pipe.close()
                except Exception:  # noqa: BLE001
                    pass

        threads = [
            threading.Thread(target=pump, args=("stdout", proc.stdout), daemon=True),
            threading.Thread(target=pump, args=("stderr", proc.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            self.returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.returncode = proc.wait()
            self._add("stderr", f"превышено время ожидания ({timeout} с) — процесс остановлен")
        for thread in threads:
            thread.join(timeout=5)
        if self.returncode:
            self._add("stderr", f"процесс завершился с кодом {self.returncode}")
        return self._report(started)

    def _report(self, started: float) -> dict:
        paths = self.save()
        return {
            "ok": not self.returncode and not self.errors,
            "returncode": self.returncode,
            "command": " ".join(self.command),
            "cwd": self.cwd,
            "duration": round(time.monotonic() - started, 3),
            "total": len(self.entries),
            "errors": self.errors,
            "report": self.to_text(self.errors),
            "log": paths["log"],
            "errors_log": paths["errors_log"],
        }
