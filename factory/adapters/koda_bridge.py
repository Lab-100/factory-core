"""Мост external_agent -> Koda (Koda Code / Koda Desktop).

Что это такое.
    Фабрика отдаёт задачи исполнителям через адаптер external_agent: он
    запускает команду, пишет ей промпт в stdin и читает ответ из stdout.
    Koda (приложение Koda Desktop и его встроенный консольный движок Koda Code)
    в неинтерактивном режиме работает так: `koda -p "<промпт>"` печатает ответ
    в stdout и завершается. Этот файл приводит Koda к общему формату
    «промпт на stdin -> ответ в stdout», чтобы фабрике было всё равно, какой
    исполнитель стоит за мостом.

Как работает (по шагам).
    1. Читает промпт из stdin (если stdin пуст — берёт аргументы командной
       строки: `koda_bridge.py "сам промпт"`).
    2. Ищет Koda в таком порядке:
         - переменная окружения KODA_CMD;
         - koda / koda.cmd в PATH;
         - типовые пути установки на этой машине (обёртка CLI @kodadev/koda-cli
           и папка Koda Desktop).
    3. Запускает `koda -p <промпт>` скрыто (CREATE_NO_WINDOW), без интерактива,
       с жёстким таймаутом — окно не мигает и не блокирует фабрику.
    4. Вырезает ANSI-коды, печатает ответ в stdout.

Переменные окружения.
    KODA_CMD             путь к koda.cmd/koda.exe (если не хочется искать)
    KODA_BRIDGE_TIMEOUT  таймаут одного запроса, секунды (по умолчанию 180)

Коды возврата.
    0 — ответ получен и напечатан;
    2 — Koda не найден, ответ пустой или запрос не уложился в таймаут
        (фабрика зафиксирует отказ и перейдёт к следующему исполнителю).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = int(os.environ.get("KODA_BRIDGE_TIMEOUT", "180"))

# Управляющие последовательности терминала (цвета, курсор) — в ответ фабрике
# они не нужны и только мешают сравнивать текст.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _candidates() -> list[str]:
    """Все места, где может лежать консольный движок Koda, по приоритету."""
    items: list[str] = []
    configured = os.environ.get("KODA_CMD", "").strip()
    if configured:
        items.append(configured)
    for name in ("koda", "koda.cmd"):
        found = shutil.which(name)
        if found:
            items.append(found)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        items.append(str(Path(local) / "hermes" / "node" / "koda.cmd"))
        items.append(str(Path(local) / "hermes" / "node" / "koda.exe"))
        items.append(str(Path(local) / "Programs" / "koda-app" / "Koda Desktop.exe"))
    home = Path.home()
    items.append(str(home / "AppData" / "Local" / "hermes" / "node" / "koda.cmd"))
    return items


def find_koda() -> str | None:
    """Первый существующий файл запуска Koda (или None)."""
    for item in _candidates():
        if not item:
            continue
        path = Path(item)
        if path.is_file():
            return str(path)
        found = shutil.which(item)
        if found:
            return found
    return None


def read_prompt() -> str:
    """Промпт: сначала stdin, затем аргументы командной строки."""
    data = ""
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
    except Exception:  # noqa: BLE001
        data = ""
    if not data.strip() and len(sys.argv) > 1:
        data = " ".join(sys.argv[1:])
    return data.strip()


def clean(text: str) -> str:
    """Убирает ANSI-коды и лишние пустые строки по краям."""
    return _ANSI.sub("", text).strip()


def main() -> int:
    prompt = read_prompt()
    if not prompt:
        print("koda: пустой промпт", file=sys.stderr)
        return 2

    koda = find_koda()
    if not koda:
        print("koda: команда не найдена (KODA_CMD не задан, Koda не установлена)", file=sys.stderr)
        return 2

    try:
        proc = subprocess.run(
            [koda, "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        print(f"koda: таймаут {TIMEOUT_S} с", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"koda: {exc}", file=sys.stderr)
        return 2

    answer = clean(proc.stdout or "")
    if not answer:
        detail = clean(proc.stderr or "")[:300]
        print(f"koda: пустой ответ (rc={proc.returncode}) {detail}".strip(), file=sys.stderr)
        return 2

    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
