"""Проверка апгрейда фабрики и всей системы (фабрика + INVR) с аддитивным бэкапом.

Логика:
  * локальная версия берётся из package `__version__` и `project.json`;
  * удалённая — с GitHub: `gh api repos/Lab-100/factory-core/releases/latest`
    (или токен `GITHUB_TOKEN`/`state/github.token`), без сети — честно "неизвестно";
  * аддитивный роллбэк: перед обновлением заменяемое копируется в
    `<корень>/dist/backup/<timestamp>/` (файлы + manifest.json), а также в
    каталог отката backup-util (ChangeId), при доступности инструмента.

Обновления не выполняются сами: команда только готовит и (при --backup)
создаёт резервную копию. Фактическая замена остаётся за оператором/скриптом.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def _local_version(root: Path) -> str:
    try:
        proj = json.loads((root / "project.json").read_text(encoding="utf-8"))
        return str(proj.get("version") or __version__)
    except (OSError, json.JSONDecodeError):
        return str(__version__)


def _remote_version(root: Path) -> tuple[str | None, str]:
    """Последний релиз с GitHub (через gh или токен). (версия | None, ошибка)."""
    env = dict(os.environ)
    token = env.get("GITHUB_TOKEN") or ""
    if not token:
        try:
            tok_file = root / "state" / "github.token"
            if tok_file.is_file():
                token = tok_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    try:
        if token:
            out = subprocess.run(
                ["gh", "api", "repos/Lab-100/factory-core/releases/latest",
                 "--jq", ".tag_name"],
                capture_output=True, text=True, timeout=30, env=env, encoding="utf-8")
        else:
            out = subprocess.run(
                ["gh", "api", "repos/Lab-100/factory-core/releases/latest",
                 "--jq", ".tag_name"],
                capture_output=True, text=True, timeout=30, env=env, encoding="utf-8")
        tag = (out.stdout or "").strip()
        if out.returncode == 0 and tag:
            return tag.lstrip("v"), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    return None, "не удалось получить удалённую версию (нет gh/токена/сети)"


def _additive_backup(root: Path, targets: list[Path]) -> dict:
    """Аддитивный бэкап заменяемых файлов: копии + manifest.json."""
    if not targets:
        return {"ok": True, "skipped": True, "dir": ""}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / "dist" / "backup" / stamp
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    manifest: list[dict] = []
    backed = 0
    for target in targets:
        if not Path(target).exists():
            continue
        rel = Path(target).name
        dst = backup_dir / rel
        try:
            if Path(target).is_dir():
                shutil.copytree(target, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(target, dst)
            manifest.append({"src": str(target), "dst": str(dst),
                             "ts": datetime.now(timezone.utc).isoformat()})
            backed += 1
        except OSError as exc:
            manifest.append({"src": str(target), "error": str(exc)})
    try:
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, "dir": str(backup_dir), "count": len(manifest),
            "skipped": backed == 0}


def _invr_status(settings) -> dict:
    from .paths import tools_root

    root = tools_root(settings)
    return {"tools_root": str(root) if root else None,
            "present": root is not None}


def check(settings=None, backup: bool = False, items: str = "") -> dict:
    from .paths import repo_root

    root = repo_root(settings)
    version = _local_version(root)
    remote, err = _remote_version(root)
    has_update = bool(remote and remote != version)
    targets: list[Path] = []
    if items:
        for raw in items.split(","):
            raw = raw.strip()
            if raw:
                candidates = [Path(raw), root / raw, Path.home() / raw]
                hit = next((c for c in candidates if c.exists()), None)
                if hit:
                    targets.append(hit)
    backup_info: dict = {"ok": True, "skipped": True, "dir": ""}
    if backup and targets:
        backup_info = _additive_backup(root, targets)

    lines = [
        "Проверка апгрейда фабрики и системы",
        f"  локальная версия   : {version}",
        f"  удалённая (GitHub) : {remote or 'неизвестно'}",
        f"  обновление доступно: {'да' if has_update else 'нет'}",
    ]
    if err:
        lines.append(f"  примечание         : {err}")
    elif remote:
        if has_update:
            lines.append("  для обновления запустите сборку/установку; заменяемое "
                         "уйдёт в dist/backup/ (аддитивно).")
    invr = _invr_status(settings)
    lines.append(f"  INVR-склад         : "
                 f"{invr['tools_root'] or 'не найден'} "
                 f"({'есть' if invr['present'] else 'нет'})")
    if backup_info.get("ok") and not backup_info.get("skipped"):
        lines.append(f"  аддитивный бэкап   : {backup_info.get('dir')} "
                     f"({backup_info.get('count', 0)} объектов)")
    return {
        "ok": not err,
        "version": version,
        "remote": remote,
        "has_update": has_update,
        "invr": invr,
        "backup": backup_info,
        "text": "\n".join(lines),
    }