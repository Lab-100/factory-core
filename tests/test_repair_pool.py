"""Тесты ремонтного пула — без внешних вызовов (только структура/деградация)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class RepairPoolTest(unittest.TestCase):
    def test_collect_logs_handles_missing(self) -> None:
        from factory.repair_pool import collect_logs

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            lines = collect_logs(root, "job1", "ses1")
            self.assertIsInstance(lines, list)

    def test_collect_logs_reads_existing(self) -> None:
        from factory.repair_pool import collect_logs

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            (state / "jobs.jsonl").write_text('{"id":"j1"}\n', encoding="utf-8")
            lines = collect_logs(root, "j1", "")
            self.assertTrue(any("jobs.jsonl" in l for l in lines))

    def test_repair_falls_back_gracefully(self) -> None:
        from factory.repair_pool import repair

        # Без реальных команд/ключей — пул честно не ответит, но без исключений
        result = repair(None, job_id="j", goal="тест", session_id="", error="err")
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)


if __name__ == "__main__":
    unittest.main()