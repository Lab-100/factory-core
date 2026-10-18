"""Очередь задач фабрики: фоновое исполнение и подтверждения (для веб-дашборда).

Журнал задач — `state/jobs.jsonl` (по строке на каждое изменение состояния).
Полуавтомат (semi) поддержан: шаг, требующий подтверждения, ставит задачу в
статус `awaiting-approval` и ждёт решения через `approve(id, ok)`.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .context import build_context
from .dispatch import Dispatcher
from .sessions import SessionStore
from .task import Task


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _answer_text(result: dict) -> str:
    """Прямой ответ нейросетей из результата задачи (последний успешный шаг)."""
    if not isinstance(result, dict):
        return ""
    results = result.get("results") or []
    text = ""
    for r in reversed(results):
        if not isinstance(r, dict) or not r.get("ok"):
            continue
        out = r.get("output") or {}
        if not isinstance(out, dict):
            continue
        cand = (
            out.get("content") or out.get("response") or out.get("answer")
            or out.get("text") or out.get("stdout") or out.get("markdown")
        )
        if isinstance(cand, str) and cand.strip():
            text = cand.strip()
            break
    return text


class JobManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "state" / "jobs.jsonl"
        self.sessions = SessionStore(self.root)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._load()

    # --- хранение ---
    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                job = json.loads(line)
            except json.JSONDecodeError:
                continue
            if job.get("id"):
                self._jobs[job["id"]] = job

    def _persist(self, job: dict) -> None:
        job["updated"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(job, ensure_ascii=False) + "\n")

    # --- доступ ---
    def list(self) -> list[dict]:
        return sorted(self._jobs.values(), key=lambda j: j.get("created", ""), reverse=True)

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def summary(self, job: dict) -> dict:
        result = job.get("result") or {}
        return {
            "id": job.get("id"),
            "created": job.get("created"),
            "updated": job.get("updated"),
            "status": job.get("status"),
            "goal": job.get("goal"),
            "domain": job.get("domain"),
            "automation": job.get("automation"),
            "dry_run": job.get("dry_run"),
            "awaiting": job.get("awaiting", False),
            "pending": job.get("pending"),
            "error": job.get("error"),
            "session": job.get("session"),
            "ok": result.get("ok") if result else None,
            "last_step": job.get("last_step"),
            "elapsed": job.get("elapsed"),
            "answer": _answer_text(result),
        }

    # --- запуск ---
    def submit(self, payload: dict) -> dict:
        job_id = uuid.uuid4().hex[:12]
        payload = dict(payload)
        payload.setdefault("schema", "invr.task/1")
        payload.setdefault("id", f"job-{job_id}")
        job = {
            "id": job_id,
            "created": _now(),
            "updated": _now(),
            "status": "queued",
            "goal": payload.get("goal", ""),
            "domain": payload.get("domain"),
            "automation": payload.get("automation"),
            "dry_run": bool(payload.get("dry_run")),
            "awaiting": False,
            "pending": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        session_id = str(payload.get("session") or "")
        if session_id:
            job["session"] = session_id
            self.sessions.ensure(session_id)
            self.sessions.append(session_id, "user", job["goal"], job_id=job_id)
            with self._lock:
                self._persist(job)
        threading.Thread(target=self._run, args=(job_id, payload), daemon=True).start()
        return job

    def _run(self, job_id: str, payload: dict) -> None:
        job = self._jobs[job_id]
        try:
            _root, settings, registry, ledger = build_context()
            task = Task.from_dict(payload)
            dispatcher = Dispatcher(settings, registry, ledger)

            def approve(step) -> bool:
                event = threading.Event()
                self._events[job_id] = event
                job["awaiting"] = True
                job["pending"] = step.to_dict()
                job["status"] = "awaiting-approval"
                self._persist(job)
                event.wait(timeout=3600)
                decision = self._decisions.pop(job_id, False)
                job["awaiting"] = False
                job["pending"] = None
                return decision

            job["status"] = "running"
            job["last_step"] = "запуск…"
            self._persist(job)

            def on_step(step, res, phase) -> None:
                # phase: start | ok | error | unavailable | skipped | awaiting-approval | simulated
                step_name = ""
                if step is not None:
                    cap = getattr(step, "capability", "") or ""
                    adapter = getattr(step, "adapter", "") or ""
                    step_name = f"{cap} ({adapter})" if cap and adapter else (cap or adapter)
                if phase == "start":
                    job["last_step"] = f"выполняется: {step_name}…"
                else:
                    ok = bool(getattr(res, "ok", False)) if res is not None else False
                    status = getattr(res, "status", phase) if res is not None else phase
                    if ok:
                        job["last_step"] = f"готово: {step_name}"
                    else:
                        err = getattr(res, "error", None) if res is not None else None
                        job["last_step"] = f"отказ на шаге {step_name}" + (f": {err}" if err else "")
                        job["error"] = job.get("error") or err
                try:
                    start = datetime.fromisoformat(job.get("created"))
                    job["elapsed"] = round(
                        (datetime.now(timezone.utc) - start).total_seconds(), 1)
                except (ValueError, TypeError):
                    pass
                self._persist(job)

            result = dispatcher.execute(task, approve=approve,
                                        dry_run=job["dry_run"], on_step=on_step)
            job["result"] = result
            if result.get("ok"):
                job["status"] = "done"
            elif result.get("stopped"):
                job["status"] = "stopped"
            else:
                job["status"] = "failed"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = str(exc)
        finally:
            self._persist(job)
            self._record_session(job)

    def _record_session(self, job: dict) -> None:
        """Записать итог задачи в привязанную сессию (если есть)."""
        session_id = job.get("session")
        if not session_id:
            return
        result = job.get("result") or {}
        status = job.get("status")
        answer = _answer_text(result)

        if status == "done" and answer:
            # прямой ответ нейросетей — главное сообщение в чат
            lines = [answer]
        else:
            lines = [f"Задача {job.get('id')}: {status}"]
            if job.get("error"):
                lines.append(f"отказ: {job['error']}")
            elif status == "failed":
                lines.append("отказ: задача завершилась с ошибкой без ответа")
            elif status == "stopped":
                lines.append("остановлена оператором")
            if answer:
                lines.append(f"\n{answer}")
        self.sessions.append(str(session_id), "assistant", "\n".join(lines),
                             job_id=job.get("id"))

    def approve(self, job_id: str, ok: bool = True) -> dict | None:
        self._decisions[job_id] = bool(ok)
        event = self._events.get(job_id)
        if event:
            event.set()
        return self._jobs.get(job_id)

    def heal_stuck(self, stuck_after_s: float = 600) -> list[str]:
        """Авто-восстановление (watchdog): пометить зависшие задачи как failed.

        Задача считается зависшей, если слишком долго находится в статусе
        `running` или `awaiting-approval` (с момента последнего изменения).
        """
        now = datetime.now(timezone.utc)
        healed: list[str] = []
        for job_id, job in list(self._jobs.items()):
            if job.get("status") not in ("running", "awaiting-approval"):
                continue
            stamp = job.get("updated") or job.get("created")
            if not stamp:
                continue
            try:
                age = (now - datetime.fromisoformat(stamp)).total_seconds()
            except ValueError:
                continue
            if age <= stuck_after_s:
                continue
            job["status"] = "failed"
            job["awaiting"] = False
            job["pending"] = None
            job["error"] = job.get("error") or f"watchdog: задача зависла (> {int(stuck_after_s)} с)"
            event = self._events.get(job_id)
            if event:
                event.set()
            self._persist(job)
            healed.append(job_id)
        return healed
