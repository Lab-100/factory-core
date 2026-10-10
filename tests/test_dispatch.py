"""Тесты диспетчера (на подставных адаптерах, без внешних вызовов)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.adapters.base import Adapter, StepResult
from factory.dispatch import Dispatcher
from factory.domains import DomainRegistry
from factory.ledger import Ledger
from factory.settings import Settings
from factory.task import Task

BASE = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.research]
enabled = true
priority = 50
capabilities = ["web-search", "answer"]
llm = []

[domain.coding]
enabled = true
priority = 60
capabilities = ["code"]
"""

SETTINGS = {"automation": {"mode": "auto", "semi": {"checkpoints": ["scrape"]}}}


class FakeAdapter(Adapter):
    name = "lab100"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"echo": params.get("query")})


class FakeLlm(Adapter):
    name = "llm_local"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"echo": params.get("prompt")})


class DispatcherTest(unittest.TestCase):
    def _make(self, automation: str = "auto"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        base = root / "domains.toml"
        base.write_text(BASE, encoding="utf-8")
        registry = DomainRegistry(base, [])
        settings = Settings(root / "settings.toml", dict(SETTINGS))
        settings.set("automation.mode", automation)
        ledger = Ledger(root / "ledger.jsonl")
        adapters = {"lab100": FakeAdapter(settings), "llm_local": FakeLlm(settings)}
        dispatcher = Dispatcher(settings, registry, ledger, adapters=adapters)
        return tmp, dispatcher, ledger

    def test_plan_and_execute_auto(self) -> None:
        tmp, dispatcher, ledger = self._make("auto")
        try:
            task = Task(goal="найди данные", capabilities=["web-search", "answer"])
            result = dispatcher.execute(task, dry_run=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["domains"][0], "research")
            self.assertEqual(len(result["results"]), 2)
            self.assertEqual(len(ledger.tail(10)), 4)  # start + 2 steps + done
        finally:
            tmp.cleanup()

    def test_semi_stops_without_approval(self) -> None:
        tmp, dispatcher, _ = self._make("semi")
        try:
            # scrape в checkpoints, но cap даёт web-search -> не в checkpoints,
            # поэтому проверяем на примере с явным scrape
            task = Task(goal="собери", capabilities=["scrape"])
            result = dispatcher.execute(task, approve=None)
            statuses = [r["status"] for r in result["results"]]
            self.assertIn("awaiting-approval", statuses)
            self.assertTrue(result["stopped"])
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_semi_with_autoapprove(self) -> None:
        tmp, dispatcher, _ = self._make("semi")
        try:
            task = Task(goal="собери", capabilities=["scrape"])
            result = dispatcher.execute(task, approve=lambda step: True)
            self.assertTrue(all(r["ok"] for r in result["results"]))
            self.assertTrue(result["ok"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
