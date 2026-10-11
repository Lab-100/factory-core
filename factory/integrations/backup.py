"""Интеграция backup-util (ChangeId) — этап 3 дорожной карты.

Инструмент `backup-util` из склада scripts-tools фиксирует изменения и выдаёт
ChangeId. Фабрика зовёт его шим, если он найден; иначе ведёт локальный ChangeId,
чтобы запись попала в единый журнал (`ledger.jsonl`) в любом случае.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

from ..adapters.invr_tool import InvrToolAdapter


class BackupIntegration:
    name = "backup-util"

    def __init__(self, settings):
        self.settings = settings
        self._invr = InvrToolAdapter(settings)

    def shim(self) -> Path | None:
        if not self.settings.get("adapters.backup_enabled", True):
            return None
        return self._invr._shim(self.name)

    def available(self) -> bool:
        return self.shim() is not None

    def health(self) -> dict:
        return {"integration": self.name, "available": self.available(),
                "shim": str(self.shim()) if self.shim() else None,
                "sink": "backup-util" if self.shim() else "local"}

    def record(self, paths, reason: str = "factory change", task: str | None = None) -> dict:
        paths = [str(p) for p in (paths or [])]
        shim = self.shim()
        stdout = ""
        ok = False
        if shim is not None:
            try:
                proc = subprocess.run(self._argv(shim, paths, reason),
                                      capture_output=True, text=True, timeout=60)
                stdout = (proc.stdout or "").strip()
                ok = proc.returncode == 0
                if not ok:
                    stdout = (stdout + "\n" + (proc.stderr or "")).strip()
            except Exception as exc:  # noqa: BLE001
                stdout = str(exc)
        change_id = self._parse_change(stdout)
        if change_id is None:
            change_id = uuid.uuid4().hex[:12]
        if shim is None:
            source = "local"
        else:
            source = "backup-util" if ok else "backup-util:failed"
        return {
            "change_id": change_id,
            "source": source,
            "available": shim is not None,
            "ok": ok if shim is not None else True,
            "paths": paths,
            "reason": reason,
            "task": task,
            "raw": stdout[-500:],
        }

    @staticmethod
    def _argv(shim: Path, paths: list[str], reason: str) -> list[str]:
        args = ["record", "--reason", reason]
        for path in paths:
            args += ["--path", path]
        if shim.suffix.lower() == ".ps1":
            shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            return [shell, "-NoProfile", "-File", str(shim), *args]
        return [str(shim), *args]

    @staticmethod
    def _parse_change(text: str) -> str | None:
        for line in reversed((text or "").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                for key in ("change_id", "changeId", "id", "ChangeId"):
                    if data.get(key):
                        return str(data[key])
        return None
