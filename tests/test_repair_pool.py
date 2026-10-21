"""Тесты ремонтного пула — без внешних вызовов (только структура/деградация)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


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
        from factory import repair_pool

        # Без реальных команд/ключей — пул честно не ответит, но без исключений
        with mock.patch.object(repair_pool, "PROBES", []):
            result = repair_pool.repair(None, job_id="j", goal="тест",
                                        session_id="", error="err")
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)

    def test_repair_session_created(self) -> None:
        """Ремонт ведёт отдельную сессию kind='repair', скрытую из чатов."""
        from factory import repair_pool
        from factory.sessions import SessionStore

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            s = repair_pool._repair_session(root)
            self.assertEqual(s.get("kind"), "repair")
            with mock.patch.object(repair_pool, "PROBES", []):
                result = repair_pool.repair(None, job_id="j", goal="тест",
                                            session_id="", error="err")
            self.assertIn("ok", result)
            store = SessionStore(root)
            repair_list = store.list(kind="repair")
            self.assertTrue(any(x["id"] == s["id"] for x in repair_list))

    def test_run_repair_cycle_no_publish_without_repo(self) -> None:
        """Цикл ремонта деградирует, если .git рядом нет — без исключений."""
        from factory import repair_pool

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            with mock.patch.object(repair_pool, "PROBES", []), \
                    mock.patch.object(repair_pool, "_run_tool",
                                      return_value=(0, "ok (mock)")):
                res = repair_pool.run_repair_cycle(None, job_id="j", goal="тест",
                                                   session_id="", error="err",
                                                   publish=False)
            self.assertIsInstance(res, dict)
            self.assertIn("ok", res)
            self.assertEqual(res.get("tests", {}).get("ok"), True)


if __name__ == "__main__":
    unittest.main()