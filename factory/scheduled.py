"""Управление авто-надзором фабрики (задача планировщика FactoryMicromonitor).

Микромонитор (`scripts/micromonitor.ps1`) держит фабрику живой: перезапускает
`factory.exe` при падении и показывает окно «Микромонитор запуска».
Включать/отключать его нужно из дашборда кнопкой, а не руками в планировщике.

Этот модуль даёт статус и переключатель задачи (запуск/остановка), полностью
изолируя вызовы планировщика Windows.
"""
from __future__ import annotations

import subprocess
import sys

TASK_NAME = "FactoryMicromonitor"
TASK_PATH = "\\"  # корневая папка планировщика (наши задачи лежат в корне)

_IS_WIN = sys.platform.startswith("win")


def _run_schtasks(args: list[str], timeout: float = 30) -> tuple[int, str]:
    """Выполнить schtasks с тихим подавлением окна консоли."""
    if not _IS_WIN:
        return -1, "планировщик Windows доступен только на Windows"
    try:
        proc = subprocess.run(
            ["schtasks", *args],
            capture_output=True, text=True, encoding="oem", errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return -1, "schtasks не найден"
    except subprocess.TimeoutExpired:
        return -1, "таймаут schtasks"
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def status() -> dict:
    """Статус задачи авто-надзора: включена/запущена/существует."""
    if not _IS_WIN:
        return {"task": TASK_NAME, "enabled": False, "running": False,
                "exists": False, "available": False,
                "note": "планировщик Windows не доступен"}
    rc, out = _run_schtasks(["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if rc != 0:
        return {"task": TASK_NAME, "enabled": False, "running": False,
                "exists": False, "available": True}
    text = out.lower()
    # schtasks выводит "Scheduled Task State: Enabled|Disabled" и "Status: Running"
    enabled = "запущена" not in text and ("включена" in text or "enabled" in text)
    running = "running" in text or "выполняется" in text or "работает" in text
    # В русской локали: "Состояние: Включено/Отключено", "Статус: Выполняется"
    if "отключен" in text or "disabled" in text:
        enabled = False
    if "включен" in text or "enabled" in text:
        enabled = True
    return {"task": TASK_NAME, "enabled": enabled, "running": running,
            "exists": True, "available": True}


def set_enabled(on: bool) -> dict:
    """Включить/выключить авто-надзор (задача планировщика)."""
    if not _IS_WIN:
        return {"ok": False, "error": "планировщик Windows не доступен"}
    if on:
        rc, out = _run_schtasks(["/Change", "/TN", TASK_NAME, "/ENABLE"])
        if rc == 0:
            # сразу запускаем (если не запущена)
            _run_schtasks(["/Run", "/TN", TASK_NAME])
        return {"ok": rc == 0, "enabled": True, "output": out[-400:]}
    rc, out = _run_schtasks(["/Change", "/TN", TASK_NAME, "/DISABLE"])
    return {"ok": rc == 0, "enabled": False, "output": out[-400:]}


def toggle() -> dict:
    """Переключить авто-надзор (вкл->выкл, выкл->вкл)."""
    st = status()
    if not st.get("exists"):
        return {"ok": False, "error": f"задача {TASK_NAME} не найдена",
                "status": st}
    target = not st.get("enabled")
    result = set_enabled(target)
    result["status"] = status()
    return result