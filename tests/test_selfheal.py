"""Тесты самопочинки: классификация состояния и возврат к зелёному состоянию.

Git и unittest подменяются внедрённым ``run`` — реальные команды не запускаются.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.selfheal import SelfHealer


class FakeRunner:
    """Подмена подпроцессов: отдаёт заранее заданные ответы по порядку."""

    def __init__(self, tests=None, dirty=None, head: str = "abc123"):
        self.tests = list(tests or [(0, "OK")])
        self.dirty = [list(item) for item in (dirty or [[]])]
        self.commit_head = head
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout=None):  # noqa: ANN001
        self.calls.append(list(args))
        if args[:3] == ["git", "status", "--porcelain"]:
            lines = self.dirty.pop(0) if self.dirty else []
            return 0, ("\n".join(lines) + "\n" if lines else "")
        if args[:2] == ["git", "rev-parse"]:
            return 0, "abc123\n"
        if args[:2] == ["git", "stash"]:
            return 0, "Saved working directory\n"
        if args[:2] == ["git", "push"]:
            return 0, "Everything up-to-date\n"
        if args[:1] == ["git"] and args[1:2] == ["commit"]:
            return 0, "[main abc123] msg\n"
        if "unittest" in args:
            return self.tests.pop(0) if self.tests else (0, "OK")
        return 0, ""

    def ran(self, *prefix: str) -> bool:
        return any(call[:len(prefix)] == list(prefix) for call in self.calls)


class SelfHealTest(unittest.TestCase):
    def _make(self, runner: FakeRunner):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        return SelfHealer(root=root, run=runner), root

    def test_clean_when_tests_pass(self) -> None:
        runner = FakeRunner(tests=[(0, "OK")], dirty=[[" M a.py"]])
        healer, _ = self._make(runner)
        report = healer.heal(fix=True)
        self.assertEqual(report["action"], "clean")
        self.assertTrue(report["healed"])
        self.assertFalse(runner.ran("git", "stash"))

    def test_healed_by_stash(self) -> None:
        runner = FakeRunner(tests=[(1, "FAILED (failures=1)"), (0, "OK")],
                            dirty=[[" M a.py"], [" M a.py"]])
        healer, _ = self._make(runner)
        report = healer.heal(fix=True)
        self.assertEqual(report["action"], "healed-by-stash")
        self.assertTrue(report["healed"])
        self.assertTrue(str(report["stash"]).startswith("factory-selfheal-"))
        self.assertTrue(runner.ran("git", "stash", "push"))

    def test_broken_head_without_changes(self) -> None:
        runner = FakeRunner(tests=[(1, "FAILED (failures=1)")], dirty=[[]])
        healer, _ = self._make(runner)
        report = healer.heal(fix=True)
        self.assertEqual(report["action"], "broken-head")
        self.assertFalse(report["healed"])
        self.assertFalse(runner.ran("git", "stash", "push"))

    def test_restores_edits_when_still_broken(self) -> None:
        runner = FakeRunner(tests=[(1, "FAILED (failures=1)"), (1, "FAILED (errors=1)")],
                            dirty=[[" M a.py"], [" M a.py"]])
        healer, _ = self._make(runner)
        report = healer.heal(fix=True)
        self.assertEqual(report["action"], "broken-head")
        self.assertFalse(report["healed"])
        self.assertTrue(runner.ran("git", "stash", "pop"))

    def test_no_fix_only_diagnoses(self) -> None:
        runner = FakeRunner(tests=[(1, "FAILED (failures=2)")], dirty=[[" M a.py"]])
        healer, _ = self._make(runner)
        report = healer.heal(fix=False)
        self.assertEqual(report["action"], "broken")
        self.assertFalse(report["healed"])
        self.assertFalse(runner.ran("git", "stash", "push"))

    def test_journal_is_written(self) -> None:
        runner = FakeRunner(tests=[(0, "OK")], dirty=[[]])
        healer, root = self._make(runner)
        healer.heal(fix=True)
        log = (root / "state" / "selfheal.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(json.loads(log[0])["action"], "clean")

    def test_failed_count(self) -> None:
        self.assertEqual(SelfHealer._failed_count("FAILED (failures=2, errors=1)"), 3)
        self.assertEqual(SelfHealer._failed_count("OK"), 0)

    def test_commit_and_push(self) -> None:
        runner = FakeRunner(tests=[(0, "OK")], dirty=[[" M a.py"], [" M a.py"]])
        healer, _ = self._make(runner)
        commit = healer.commit("тест")
        self.assertTrue(commit["ok"])
        self.assertTrue(commit["committed"])
        push = healer.push()
        self.assertTrue(push["ok"])


if __name__ == "__main__":
    unittest.main()
