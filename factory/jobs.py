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


def _model_names(result: dict) -> list[str]:
    """Имена моделей/провайдеров, реально ответивших на шагах задачи."""
    if not isinstance(result, dict):
        return []
    found: list[str] = []
    results = result.get("results") or []
    for r in results:
        if not isinstance(r, dict) or not r.get("ok"):
            continue
        out = r.get("output") or {}
        if not isinstance(out, dict):
            continue
        name = str(out.get("provider") or out.get("model") or "").strip()
        if name and name not in found:
            found.append(name)
    return found


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
            created = self.sessions.ensure(session_id)
            # Вопрос уже добавлен в сессию оптимистично (веб-чат добавляет его
            # сразу при отправке) — не дублируем его здесь.
            already = any(
                m.get("role") == "user" and m.get("text") == job["goal"]
                for m in (created or {}).get("messages", [])
            )
            if not already:
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
            self._session_note(job, "система", "задача запущена, ждём очередь…",
                                status="queued")
            _noted: set[tuple] = set()

            def on_step(step, res, phase) -> None:
                # phase: start | ok | error | unavailable | skipped | awaiting-approval | simulated
                step_name = ""
                if step is not None:
                    cap = getattr(step, "capability", "") or ""
                    adapter = getattr(step, "adapter", "") or ""
                    step_name = f"{cap} ({adapter})" if cap and adapter else (cap or adapter)
                is_synth = bool(getattr(step, "params", {}).get("synthesize")) if step is not None else False
                if phase == "start":
                    if is_synth:
                        job["last_step"] = "оркестратор: сбор ответов нейросетей…"
                    else:
                        job["last_step"] = f"выполняется: {step_name}…"
                else:
                    ok = bool(getattr(res, "ok", False)) if res is not None else False
                    status = getattr(res, "status", phase) if res is not None else phase
                    if ok:
                        job["last_step"] = ("готово: синтез ответов (оркестратор)"
                                            if is_synth else f"готово: {step_name}")
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
                self._session_step_note(job, step_name, is_synth, phase, res, _noted)

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

    def _session_note(self, job: dict, role: str, text: str,
                      status: str | None = None) -> None:
        """Записать служебную заметку в привязанную сессию (если есть)."""
        session_id = job.get("session")
        if not session_id:
            return
        meta: dict = {}
        if status:
            meta["status"] = status
        if job.get("elapsed") is not None:
            meta["elapsed"] = job["elapsed"]
        try:
            self.sessions.append(str(session_id), str(role), str(text),
                                 job_id=job.get("id"), meta=meta or None)
        except OSError:
            pass

    def _session_step_note(self, job: dict, step_name: str, is_synth: bool,
                           phase: str, res, noted: set) -> None:
        """Промежуточный статус шага в чат: что происходит, какая модель,
        сколько прошло времени. Пишется один раз на фазу шага."""
        session_id = job.get("session")
        if not session_id:
            return
        elapsed = job.get("elapsed")
        wait = f" ({elapsed} с)" if isinstance(elapsed, (int, float)) else ""
        key = (job.get("id"), step_name, phase)
        if key in noted:
            return
        model = ""
        if res is not None:
            out = getattr(res, "output", None) or {}
            if isinstance(out, dict):
                model = str(out.get("provider") or out.get("model") or "").strip()
        shown = step_name.replace("code (external_agent)", "код (внешняя модель)")
        if phase == "start":
            if is_synth:
                text = f"🔄 синтез ответов нейросетей…{wait}"
            else:
                text = f"🔄 {shown}…{wait}"
        else:
            ok = bool(getattr(res, "ok", False)) if res is not None else False
            if ok:
                text = f"✅ {shown} — готово{wait}"
                if model:
                    text += f" (модель: {model})"
            else:
                err = getattr(res, "error", None) if res is not None else None
                tail = f": {err}" if err else ""
                text = f"❌ {shown}{tail}{wait}"
        if text:
            noted.add(key)
            self._session_note(job, "система", text, status=phase)

    def _record_session(self, job: dict) -> None:
        """Записать итог задачи в привязанную сессию (если есть)."""
        session_id = job.get("session")
        if not session_id:
            return
        result = job.get("result") or {}
        status = job.get("status")
        answer = _answer_text(result)
        elapsed = job.get("elapsed")
        wait = f" за {elapsed} с" if isinstance(elapsed, (int, float)) else ""
        model = _model_names(result)

        done = status == "done"
        head = "✅ Задача выполнена" if done else f"Задача {job.get('id')}: {status}"
        if model:
            head += f" — модель(и): {', '.join(model)}"
        if wait:
            head += wait

        if done and answer:
            lines = [answer]
            if model or elapsed is not None:
                lines.append(f"\n— {head}")
        else:
            lines = [head]
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
