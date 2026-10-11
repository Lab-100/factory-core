"""Экспорт результатов фабрики: заказ-наряд и сессия — в markdown, JSON или ZIP.

Точка входа — функции :func:`export_job` и :func:`export_session`; обе возвращают
кортеж ``(данные: bytes, mime-тип: str, имя файла: str)`` либо ``None``, если
объект не найден. ZIP-архив заказа включает ``job.json``, ``job.md`` и файлы
каталога артефактов (если он есть). Внешних вызовов нет.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path


def _job(root: str | Path, job_id: str) -> dict | None:
    from .jobs import JobManager
    return JobManager(root).get(str(job_id))


def _session(root: str | Path, session_id: str) -> dict | None:
    from .sessions import SessionStore
    return SessionStore(root).get(str(session_id))


def job_markdown(job: dict) -> str:
    """Человекочитаемый отчёт по заказу."""
    result = job.get("result") or {}
    lines = [f"# Задача {job.get('id')}", "",
             f"- Статус: {job.get('status')}",
             f"- Цель: {job.get('goal')}",
             f"- Домен: {job.get('domain') or 'auto'}",
             f"- Режим: {job.get('automation') or 'по настройке'}",
             f"- Создана: {job.get('created')}",
             f"- Обновлена: {job.get('updated')}"]
    if job.get("session"):
        lines.append(f"- Сессия: {job['session']}")
    if isinstance(result, dict):
        lines.append(f"- Итог: {'успех' if result.get('ok') else 'неуспех'}")
        catalog = result.get("catalog")
        if catalog:
            lines.append(f"- Каталог: {catalog}")
        steps = result.get("results") or result.get("steps") or []
        if steps:
            lines += ["", "## Шаги"]
            for item in steps:
                if isinstance(item, dict):
                    status = item.get("status") or ("ok" if item.get("ok") else "?")
                    name = item.get("capability") or item.get("action") or item.get("adapter")
                    lines.append(f"- [{status}] {name}")
    if job.get("error"):
        lines += ["", "## Ошибка", str(job["error"])]
    return "\n".join(lines) + "\n"


def session_markdown(session: dict) -> str:
    """Диалоговый поток сессии в markdown."""
    role_names = {"user": "оператор", "assistant": "фабрика", "system": "система"}
    lines = [f"# Сессия {session.get('id')} — {session.get('title', '')}", "",
             f"- Проект: {session.get('project') or '—'}",
             f"- Создана: {session.get('created')}",
             f"- Обновлена: {session.get('updated')}",
             f"- Сообщений: {len(session.get('messages') or [])}", ""]
    for message in session.get("messages") or []:
        role = role_names.get(message.get("role"), message.get("role"))
        lines.append(f"## {role} · {message.get('at', '')}")
        lines.append(str(message.get("text", "")))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _zip_catalog(zf: zipfile.ZipFile, root: Path, catalog: str) -> None:
    base = Path(catalog)
    if not base.is_absolute():
        base = root / base
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        if path.is_file():
            arc = Path("catalog") / base.name / path.relative_to(base)
            zf.write(path, arc.as_posix())


def export_job(root: str | Path, job_id: str, fmt: str = "md"):
    """Экспорт заказа: (bytes, mime, filename) или None."""
    job = _job(root, str(job_id))
    if job is None:
        return None
    fmt = (fmt or "md").lower()
    if fmt == "json":
        return _json_bytes(job), "application/json", f"job-{job['id']}.json"
    if fmt == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buf := buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"job-{job['id']}.json", _json_bytes(job))
            zf.writestr(f"job-{job['id']}.md", job_markdown(job).encode("utf-8"))
            catalog = (job.get("result") or {}).get("catalog") if isinstance(job.get("result"), dict) else None
            if catalog:
                _zip_catalog(zf, Path(root), str(catalog))
        return buf.getvalue(), "application/zip", f"job-{job['id']}.zip"
    return job_markdown(job).encode("utf-8"), "text/markdown; charset=utf-8", f"job-{job['id']}.md"


def export_session(root: str | Path, session_id: str, fmt: str = "md") -> tuple[bytes, str, str] | None:
    """Экспорт сессии в markdown или JSON."""
    session = _session(root, session_id)
    if session is None:
        return None
    fmt = (fmt or "md").lower()
    if fmt == "json":
        return _json_bytes(session), "application/json", f"session-{session_id}.json"
    return (session_markdown(session).encode("utf-8"),
            "text/markdown; charset=utf-8", f"session-{session_id}.md")


def export(root: str | Path, kind: str, obj_id: str,
           fmt: str = "md") -> tuple[bytes, str, str] | None:
    """Общая точка: kind — ``job`` или ``session``."""
    if str(kind) == "session":
        return export_session(root, obj_id, fmt)
    return export_job(root, obj_id, fmt)
