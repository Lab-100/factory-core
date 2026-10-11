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


if __name__ == "__main__":
    unittest.main()
