"""Тесты очереди задач (веб-дашборд): фоновое исполнение и подтверждения.

Проверяет то, что раньше не покрывалось: фоновый прогон задачи, режим
полуавтомата с подтверждением шага через `approve`, переживание перезапуска
менеджера и авто-восстановление зависших задач (watchdog).
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from factory.domains import DomainRegistry
from factory.jobs import JobManager
from factory.ledger import Ledger
from factory.settings import Settings

DOMAINS = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.research]
enabled = true
priority = 50
capabilities = ["web-search", "answer"]
"""

TERMINAL = {"done", "failed", "error", "stopped"}


class JobManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        domains_file = self.root / "config" / "domains.toml"
        domains_file.write_text(DOMAINS, encoding="utf-8")
        self.registry = DomainRegistry(domains_file, [])
        self.settings = Settings(
            self.root / "config" / "settings.toml",
            {"automation": {"mode": "auto", "semi": {"checkpoints": []}}},
        )
        self.ledger = Ledger(self.root / "state" / "ledger.jsonl")
        # `_run` работает в отдельном потоке и зовёт глобальный build_context(),
        # поэтому подменяем его на изолированный контекст теста.
        self._patch = mock.patch(
            "factory.jobs.build_context",
            return_value=(self.root, self.settings, self.registry, self.ledger),
        )
        self._patch.start()
        self.manager = JobManager(self.root)

    def tearDown(self) -> None:
        self._patch.stop()
        self.tmp.cleanup()

    def _wait(self, job_id: str, statuses: set[str], timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.manager.get(job_id)
            if job and job.get("status") in statuses:
                return job
            time.sleep(0.02)
        raise AssertionError(f"задача {job_id} не достигла {statuses}: {self.manager.get(job_id)}")

    def test_auto_job_completes(self) -> None:
        job = self.manager.submit(
            {"goal": "найди данные", "capabilities": ["web-search"], "dry_run": True})
        done = self._wait(job["id"], TERMINAL)
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["result"]["ok"])
        self.assertIn("research", done["result"]["domains"])
        self.assertTrue((self.root / "state" / "jobs.jsonl").exists())

    def test_semi_awaits_then_approval_finishes(self) -> None:
        job = self.manager.submit(
            {"goal": "найди", "capabilities": ["web-search"],
             "dry_run": True, "automation": "semi"})
        waiting = self._wait(job["id"], TERMINAL | {"awaiting-approval"})
        self.assertEqual(waiting["status"], "awaiting-approval")
        self.assertTrue(waiting["awaiting"])
        self.assertIsNotNone(waiting["pending"])

        self.manager.approve(job["id"], ok=True)
        done = self._wait(job["id"], TERMINAL)
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["result"]["ok"])
        self.assertFalse(done["awaiting"])

    def test_job_survives_manager_restart(self) -> None:
        job = self.manager.submit(
            {"goal": "ответь", "capabilities": ["answer"], "dry_run": True})
        done = self._wait(job["id"], TERMINAL)
        fresh = JobManager(self.root)
        restored = fresh.get(done["id"])
        self.assertIsNotNone(restored)
        self.assertEqual(restored["status"], "done")

    def test_summary_shape(self) -> None:
        job = self.manager.submit(
            {"goal": "ответь", "capabilities": ["answer"], "dry_run": True})
        done = self._wait(job["id"], TERMINAL)
        summary = self.manager.summary(done)
        self.assertEqual(summary["id"], job["id"])
        self.assertEqual(summary["status"], "done")
        self.assertTrue(summary["ok"])

    def test_heal_stuck_marks_failed(self) -> None:
        state = self.root / "state"
        state.mkdir(parents=True, exist_ok=True)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        (state / "jobs.jsonl").write_text(
            json.dumps({"id": "stuck1", "created": old, "updated": old,
                        "status": "running", "goal": "завис"}) + "\n",
            encoding="utf-8",
        )
        manager = JobManager(self.root)
        healed = manager.heal_stuck(600)
        self.assertEqual(healed, ["stuck1"])
        self.assertEqual(manager.get("stuck1")["status"], "failed")

    def test_heal_ignores_recent_job(self) -> None:
        job = self.manager.submit(
            {"goal": "свежая", "capabilities": ["answer"], "dry_run": True})
        done = self._wait(job["id"], TERMINAL)
        self.assertEqual(self.manager.heal_stuck(600), [])
        self.assertEqual(self.manager.get(done["id"])["status"], "done")

    def test_session_receives_status_messages_and_answer(self) -> None:
        """Чат получает сообщения-статусы (что происходит, какая модель,
        сколько времени) и итоговый ответ с указанием модели и длительности."""
        job = self.manager.submit(
            {"goal": "ответь одним словом", "capabilities": ["answer"],
             "dry_run": True, "session": "ses-chat-1"})
        session = self.manager.sessions.ensure("ses-chat-1")
        self.assertIsNotNone(session)
        done = self._wait(job["id"], TERMINAL)
        # перечитываем сессию после завершения задачи
        session = self.manager.sessions.get("ses-chat-1")
        self.assertIsNotNone(session)
        messages = session["messages"]
        # пользовательское сообщение появилось сразу при запуске
        self.assertTrue(any(m["role"] == "user" for m in messages))
        # промежуточные статусы (система) есть
        self.assertTrue(any(m["role"] == "system" for m in messages))
        # финальное сообщение ассистента — есть
        final = [m for m in messages if m["role"] == "assistant"]
        self.assertTrue(final, "в сессии нет итогового сообщения ассистента")
        self.assertEqual(done["status"], "done")
        text = final[-1]["text"]
        self.assertIn("Задача выполнена", text)
        self.assertIn("с", text)  # длительность в секундах указана

    def test_session_step_notes_deduplicated(self) -> None:
        """Промежуточные статусы пишутся один раз на фазу шага (не дублируются)."""
        job = {"id": "job-dedup", "session": "ses-dedup",
               "created": "2026-10-19T00:00:00+00:00", "elapsed": 2.0}
        self.manager.sessions.create(session_id="ses-dedup")
        noted: set = set()
        self.manager._session_step_note(job, "code (external_agent)", False,
                                        "start", None, noted)
        self.manager._session_step_note(job, "code (external_agent)", False,
                                        "start", None, noted)
        self.manager._session_step_note(job, "code (external_agent)", False,
                                        "ok", StepResultStub(), noted)
        s = self.manager.sessions.get("ses-dedup")
        self.assertEqual(len(s["messages"]), 2)
        texts = [m["text"] for m in s["messages"]]
        self.assertIn("🔄 код (внешняя модель)… (2.0 с)", texts[0])
        self.assertIn("✅", texts[1])

    def test_submit_does_not_duplicate_optimistic_user_message(self) -> None:
        """Веб-чат добавляет вопрос в сессию сразу при отправке; submit()
        не должен дублировать уже добавленное user-сообщение."""
        sid = "ses-opt-1"
        self.manager.sessions.create(session_id=sid)
        self.manager.sessions.append(sid, "user", "мой вопрос", job_id="old")
        job = self.manager.submit(
            {"goal": "мой вопрос", "capabilities": ["answer"],
             "dry_run": True, "session": sid})
        self._wait(job["id"], TERMINAL)
        s = self.manager.sessions.get(sid)
        user_msgs = [m for m in s["messages"]
                     if m["role"] == "user" and m["text"] == "мой вопрос"]
        self.assertEqual(len(user_msgs), 1, "вопрос продублирован")

    def test_new_bridge_has_stdin_and_orphan_cleanup(self) -> None:
        """Мост external_agent->WorkAI: нативный stdin-режим (надёжная
        доставка вопроса) и распознавание осиротевших окон WorkAI."""
        import importlib.util
        from pathlib import Path
        bridge_path = (Path(__file__).resolve().parent.parent
                       / "factory" / "adapters" / "workai_bridge.py")
        self.assertTrue(bridge_path.exists(), "workai_bridge.py должен лежать в пакете")
        src = bridge_path.read_text(encoding="utf-8")
        self.assertIn("[WORKAI, \"chat\", prompt, \"-\"", src)
        self.assertIn("_workai_orphans", src)
        spec = importlib.util.spec_from_file_location("workai_bridge", bridge_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, "_workai_orphans"))
        self.assertTrue(hasattr(mod, "_kill_tree"))
        self.assertTrue(hasattr(mod, "launch_workai_native"))


class StepResultStub:
    ok = True
    output = {"provider": "cline"}
    error = None


if __name__ == "__main__":
    unittest.main()
