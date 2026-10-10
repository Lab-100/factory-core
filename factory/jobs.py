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
from .task import Task


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "state" / "jobs.jsonl"
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
            "ok": (job.get("result") or {}).get("ok") if job.get("result") else None,
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
            self._persist(job)
            result = dispatcher.execute(task, approve=approve, dry_run=job["dry_run"])
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
