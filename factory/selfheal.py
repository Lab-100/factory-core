"""Самопочинка рабочей копии: прогон тестов и возврат к зелёному состоянию.

Механизм рассчитан на работу без участия человека (планировщик задач Windows,
``factory heal --fix``) и решает одну задачу: рабочая копия репозитория должна
всегда находиться в зелёном состоянии.

Порядок работы:

* прогон полного набора тестов (``python -m unittest discover -s tests``);
* если тесты зелёные — ничего не меняется;
* если тесты падают и виноваты незакоммиченные правки — они аккуратно
  откладываются в ``git stash`` с меткой ``factory-selfheal-<время>``; если
  после этого тесты зелёные, копия считается вылеченной (чужие правки не
  теряются — они ждут в стеше);
* если падает и чистый ``HEAD`` — автоматическая починка невозможна: пишется
  тревога в ``state/selfheal.jsonl`` и в единый журнал (``kind = "selfheal"``).

Поведение никогда не разрушительно: без ``--commit`` ничего не коммитится, а
отложенные правки всегда можно вернуть командой ``git stash pop``.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .ledger import Ledger
from .paths import repo_root


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _last_line(text: str) -> str:
    """Последняя непустая строка вывода — краткая суть для отчёта."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


class SelfHealer:
    """Прогон тестов и починка рабочей копии (см. описание модуля).

    ``run`` — точка внедрения подпроцессов для тестов: ``run(args, timeout)``
    возвращает ``(returncode, вывод)``. По умолчанию запускается ``subprocess``.
    """

    def __init__(self, root: str | Path | None = None, settings=None,
                 ledger: Ledger | None = None, run=None):
        self.root = Path(root) if root else repo_root()
        self.settings = settings
        self.ledger = ledger
        self.log_path = self.root / "state" / "selfheal.jsonl"
        self._run_impl = run

    # --- вспомогательное ---
    def _setting(self, key: str, default=None):
        if self.settings is None:
            return default
        return self.settings.get(key, default)

    @property
    def timeout(self) -> float:
        try:
            return float(self._setting("heal.timeout", 900) or 900)
        except (TypeError, ValueError):
            return 900.0

    def python(self) -> list[str]:
        """Команда интерпретатора Python: настройка ``heal.python`` или текущий/``py``."""
        configured = str(self._setting("heal.python", "") or "").strip()
        if configured:
            return [configured]
        if self._run_impl is not None:
            return ["python"]
        current = Path(sys.executable or "python").name.lower()
        if current.startswith("python"):
            return [sys.executable]
        for name in ("py", "python", "python3"):
            found = shutil.which(name)
            if found:
                return [found]
        return [sys.executable or "python"]

    def tests_command(self) -> list[str]:
        suite = str(self._setting("heal.tests", "tests") or "tests")
        return [*self.python(), "-m", "unittest", "discover", "-s", suite]

    def run(self, args: list[str], timeout: float | None = None) -> tuple[int, str]:
        if self._run_impl is not None:
            return self._run_impl(list(args), timeout)
        try:
            proc = subprocess.run(args, cwd=str(self.root), capture_output=True,
                                  text=True, timeout=timeout or self.timeout, check=False)
        except FileNotFoundError:
            return 127, f"команда не найдена: {args[0]}"
        except subprocess.TimeoutExpired:
            return 124, f"таймаут {int(timeout or self.timeout)} с: {' '.join(args)}"
        except OSError as exc:  # noqa: BLE001 - сбой запуска не должен ронять службу
            return 127, f"{type(exc).__name__}: {exc}"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    # --- git ---
    def git_available(self) -> bool:
        return self._run_impl is not None or shutil.which("git") is not None

    def status_lines(self) -> list[str]:
        """Незакоммиченные изменения (строки ``git status --porcelain``)."""
        code, output = self.run(["git", "status", "--porcelain"])
        if code != 0:
            return []
        return [line for line in output.splitlines() if line.strip()]

    def head(self) -> str:
        code, output = self.run(["git", "rev-parse", "--short", "HEAD"])
        if code != 0:
            return ""
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return lines[0] if lines else ""

    def stash(self, label: str) -> bool:
        code, _ = self.run(["git", "stash", "push", "--include-untracked", "-m", label])
        return code == 0

    def stash_pop(self) -> bool:
        code, _ = self.run(["git", "stash", "pop"])
        return code == 0

    def commit(self, message: str) -> dict:
        """Зафиксировать все изменения; «нечего коммитить» — не ошибка."""
        code, output = self.run(["git", "add", "-A"])
        if code != 0:
            return {"ok": False, "committed": False, "detail": _last_line(output)}
        code, output = self.run(["git", "commit", "-m", message])
        if code != 0:
            detail = _last_line(output)
            if "nothing to commit" in output or "нечего коммитить" in output:
                return {"ok": True, "committed": False, "detail": detail}
            return {"ok": False, "committed": False, "detail": detail}
        return {"ok": True, "committed": True, "head": self.head(),
                "detail": _last_line(output)}

    def push(self) -> dict:
        code, output = self.run(["git", "push"])
        return {"ok": code == 0, "detail": _last_line(output)}

    # --- тесты ---
    def tests(self, timeout: float | None = None) -> dict:
        command = self.tests_command()
        code, output = self.run(command, timeout)
        ok = code == 0 and "OK" in output
        return {
            "ok": ok,
            "returncode": code,
            "failed": 0 if ok else self._failed_count(output),
            "command": " ".join(command),
            "output": output,
        }

    @staticmethod
    def _failed_count(output: str) -> int:
        """Число падений из итоговой строки unittest (``FAILED (failures=1, ...)``)."""
        summary = ""
        for line in output.splitlines():
            if line.strip().startswith("FAILED"):
                summary = line
        total = 0
        for match in re.finditer(r"(?:failures|errors)=(\d+)", summary):
            total += int(match.group(1))
        return total

    # --- диагностика и починка ---
    def diagnose(self, timeout: float | None = None) -> dict:
        run = self.tests(timeout=timeout)
        return {
            "tests": {key: run[key] for key in ("ok", "failed", "returncode")},
            "dirty": len(self.status_lines()),
            "head": self.head(),
        }

    def heal(self, *, fix: bool = False, timeout: float | None = None,
             message: str | None = None) -> dict:
        """Прогнать тесты и (при ``fix``) вернуть копию к зелёному состоянию."""
        run = self.tests(timeout=timeout)
        dirty = self.status_lines()
        report = {
            "action": "clean" if run["ok"] else "broken",
            "healed": bool(run["ok"]),
            "tests": {key: run[key] for key in ("ok", "failed", "returncode")},
            "dirty": len(dirty),
            "head": self.head(),
            "stash": None,
            "message": message,
        }
        if run["ok"] or not fix:
            self._record(report)
            return report
        if not dirty:
            report["action"] = "broken-head"
            self._record(report)
            return report
        label = message or f"factory-selfheal-{_stamp()}"
        if not self.stash(label):
            report["action"] = "stash-failed"
            self._record(report)
            return report
        report["stash"] = label
        retry = self.tests(timeout=timeout)
        report["tests"] = {key: retry[key] for key in ("ok", "failed", "returncode")}
        if retry["ok"]:
            report["action"] = "healed-by-stash"
            report["healed"] = True
            report["dirty"] = 0
        else:
            self.stash_pop()
            report["action"] = "broken-head"
            report["stash"] = None
            report["dirty"] = len(self.status_lines())
        self._record(report)
        return report

    def _record(self, report: dict) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(report, ensure_ascii=False) + "\n")
        except OSError:
            pass
        if self.ledger is not None:
            try:
                self.ledger.append({"kind": "selfheal", **{
                    key: report.get(key) for key in
                    ("action", "healed", "dirty", "stash", "head")}})
            except OSError:
                pass
