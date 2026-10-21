"""Обёртка фабрики над backup-утилитой (каталог отката, ChangeId).

backup-util — утилита бэкапа/отката из склада INVR (`INVR_TOOLS_ROOT/backup-util`).
Вызывается через PowerShell; при отсутствии инструмента честно сообщает, что
локализованный эквивалент не найден (не падая).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .paths import tools_root


def _script(settings=None) -> Path | None:
    root = tools_root()
    if root is None:
        return None
    for cand in (root / "backup-util" / "backup-util.ps1",
                 root / "backup-util.ps1"):
        if cand.is_file():
            return cand
    return None


def _shell() -> list[str]:
    import shutil
    sh = shutil.which("pwsh") or shutil.which("powershell") or ""
    if not sh:
        return []
    return [sh, "-NoProfile", "-File"]


def available(settings=None) -> bool:
    return _script(settings) is not None and bool(_shell())


def _run(settings, args: list[str], timeout: float = 120) -> dict:
    script = _script(settings)
    if script is None:
        return {"ok": False, "error": "backup-util не найден в складе INVR"}
    shell = _shell()
    if not shell:
        return {"ok": False, "error": "PowerShell не найден"}
    cmd = [*shell, str(script), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": out, "stderr": err}


def backup(settings=None, files: list[str] | None = None, notes: str = "") -> dict:
    files = files or []
    if not files:
        return {"ok": False, "error": "укажите файлы (--file)"}
    args = ["backup", "-Files", *files]
    if notes:
        args += ["-Notes", notes]
    return _run(settings, args)


def list_(settings=None) -> dict:
    return _run(settings, ["list"])


def init(settings=None) -> dict:
    return _run(settings, ["init"])


def undo(settings=None, change_id: str = "") -> dict:
    if not change_id:
        return {"ok": False, "error": "укажите ChangeId (--id)"}
    return _run(settings, ["undo", "-ChangeId", change_id])