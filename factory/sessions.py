"""Сессии фабрики — история запросов и ответов по задачам.

Сессия объединяет запросы оператора и результаты их выполнения в один
диалоговый поток с сохранением контекста. Хранение — по файлу на сессию:
``state/sessions/<id>.json`` (схема ``invr.session/1``).

Сообщение — словарь ``{role, text, at, job_id}``, где ``role``:
  * ``user``      — запрос оператора (goal задачи);
  * ``assistant`` — результат выполнения задачи (статус, каталог, ошибка);
  * ``system``    — служебная отметка.

Модуль не зависит от внешних сервисов и не обращается к сети.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "invr.session/1"
ROLES = ("user", "assistant", "system")
DEFAULT_TITLE = "Новая сессия"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bump(stamp: str) -> str:
    """Метка времени на одну микросекунду позже прежней.

    Системные часы Windows идут крупным шагом (~15 мс), и две записи подряд
    могли получить одинаковую метку — порядок списка сессий становился
    случайным. Сдвиг на микросекунду сохраняет порядок однозначным.
    """
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return stamp
    return (value + timedelta(microseconds=1)).isoformat()


class SessionStore:
    """Файловое хранилище сессий (по одному JSON на сессию)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = self.root / "state" / "sessions"

    # --- пути и ввод-вывод ---
    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def _read(self, session_id: str) -> dict | None:
        path = self._path(str(session_id))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        data.setdefault("messages", [])
        return data

    def _latest_updated(self) -> str:
        """Самая свежая метка обновления среди сессий хранилища."""
        latest = ""
        if not self.dir.exists():
            return latest
        for path in self.dir.glob("*.json"):
            data = self._read(path.stem)
            if not data:
                continue
            stamp = str(data.get("updated") or "")
            if stamp > latest:
                latest = stamp
        return latest

    def _next_stamp(self) -> str:
        """Метка времени, строго большая любой прежней в этом хранилище.

        Часы Windows идут крупным шагом (~15 мс), и две записи подряд могли
        получить одинаковую метку — порядок списка сессий (сортировка по
        ``updated``) становился случайным. Сдвиг вперёд сохраняет порядок
        записи однозначным.
        """
        now = _now()
        latest = self._latest_updated()
        if latest and now <= latest:
            return _bump(latest)
        return now

    def _write(self, session: dict) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        session["schema"] = SCHEMA
        session["updated"] = self._next_stamp()
        self._path(session["id"]).write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return session

    # --- операции ---
    def create(self, title: str = "", project: str = "",
               session_id: str | None = None, kind: str = "chat") -> dict:
        sid = str(session_id) if session_id else "ses" + uuid.uuid4().hex[:12]
        session = {
            "id": sid,
            "title": str(title) or DEFAULT_TITLE,
            "project": str(project) or "",
            "kind": str(kind or "chat"),
            "created": _now(),
            "updated": _now(),
            "messages": [],
        }
        return self._write(session)

    def ensure(self, session_id: str, title: str = "", project: str = "",
               kind: str = "chat") -> dict:
        """Вернуть сессию, создав её при отсутствии (для привязки задачи)."""
        return self._read(session_id) or self.create(
            title=title, project=project, session_id=session_id, kind=kind)

    def get(self, session_id: str) -> dict | None:
        return self._read(str(session_id))

    def list(self, kind: str | None = None) -> list[dict]:
        if not self.dir.exists():
            return []
        out: list[dict] = []
        for path in self.dir.glob("*.json"):
            data = self._read(path.stem)
            if data is None:
                continue
            item_kind = data.get("kind") or "chat"
            if kind is not None and item_kind != kind:
                continue
            out.append(self.summary(data))
        out.sort(key=lambda item: item.get("updated", ""), reverse=True)
        return out

    def append(self, session_id: str, role: str, text: str,
               job_id: str | None = None, meta: dict | None = None) -> dict | None:
        session = self._read(str(session_id))
        if session is None:
            return None
        if role not in ROLES:
            role = "system"
        message: dict = {
            "role": role,
            "text": str(text),
            "at": _now(),
            "job_id": job_id,
        }
        if meta:
            message["meta"] = dict(meta)
        session["messages"].append(message)
        # Автозаголовок — из первого вопроса оператора.
        if role == "user" and text and session.get("title", DEFAULT_TITLE) == DEFAULT_TITLE:
            session["title"] = str(text).strip()[:60] or DEFAULT_TITLE
        return self._write(session)

    def rename(self, session_id: str, title: str) -> dict | None:
        session = self._read(str(session_id))
        if session is None:
            return None
        session["title"] = str(title) or session.get("title", DEFAULT_TITLE)
        return self._write(session)

    def delete(self, session_id: str) -> bool:
        path = self._path(str(session_id))
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    @staticmethod
    def summary(session: dict) -> dict:
        messages = session.get("messages") or []
        last = messages[-1] if messages else {}
        return {
            "id": session.get("id"),
            "title": session.get("title", DEFAULT_TITLE),
            "project": session.get("project", ""),
            "kind": session.get("kind") or "chat",
            "created": session.get("created"),
            "updated": session.get("updated"),
            "count": len(messages),
            "last": (str(last.get("text", ""))[:120] if last else ""),
            "last_role": last.get("role") if last else None,
        }
