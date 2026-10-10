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
region = "auto"
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


class FakeSourceAdapter(Adapter):
    """Как Lab100: успешно вернул «источник: …» в stdout."""

    name = "lab100"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action,
                          {"stdout": "категория: web-search | источник: local-browser\n"})


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
        dispatcher = Dispatcher(settings, registry, ledger, adapters=adapters, integrations={})
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

    def test_region_step_uses_task_region(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди", capabilities=["web-search"],
                        inputs={"region": "DE", "zone": "eu"},
                        constraints={"allow_region_switch": True})
            _, steps = dispatcher.plan(task)
            region_steps = [s for s in steps if s.capability == "region-switch"]
            self.assertEqual(len(region_steps), 1)
            self.assertEqual(region_steps[0].params["region"], "DE")
            self.assertEqual(region_steps[0].params["zone"], "eu")
        finally:
            tmp.cleanup()

    def test_region_step_default_from_settings(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди", capabilities=["web-search"],
                        constraints={"allow_region_switch": True})
            _, steps = dispatcher.plan(task)
            region_steps = [s for s in steps if s.capability == "region-switch"]
            self.assertEqual(len(region_steps), 1)
            self.assertEqual(region_steps[0].params["region"], "US")
        finally:
            tmp.cleanup()

    def test_no_region_step_without_permission(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди", capabilities=["web-search"])
            _, steps = dispatcher.plan(task)
            self.assertFalse(any(s.capability == "region-switch" for s in steps))
        finally:
            tmp.cleanup()

    def _make_isolated(self, adapter_cls=FakeAdapter):
        """Диспетчер в изолированном каталоге (проверка записи квот)."""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config").mkdir()
        domains_file = root / "config" / "domains.toml"
        domains_file.write_text(
            'schema = "invr.factory.domains/1"\nmatch_by = "auto"\n'
            '[domain.research]\nenabled = true\npriority = 50\n'
            'capabilities = ["web-search"]\nllm = ["exa", "firecrawl"]\n',
            encoding="utf-8",
        )
        registry = DomainRegistry(domains_file, [])
        settings = Settings(root / "config" / "settings.toml",
                            {"automation": {"mode": "auto", "semi": {"checkpoints": []}}})
        adapters = {"lab100": adapter_cls(settings)}
        dispatcher = Dispatcher(settings, registry, Ledger(root / "ledger.jsonl"),
                                adapters=adapters, integrations={})
        return tmp, settings, dispatcher

    def test_explicit_provider_passed_to_step(self) -> None:
        tmp, _, dispatcher = self._make_isolated()
        try:
            task = Task(goal="найди", capabilities=["web-search"],
                        inputs={"provider": "exa"})
            _, steps = dispatcher.plan(task)
            self.assertEqual(steps[0].params["provider"], "exa")
        finally:
            tmp.cleanup()

    def test_quota_accounted_from_explicit_provider(self) -> None:
        from factory import quotas

        tmp, settings, dispatcher = self._make_isolated()
        try:
            task = Task(goal="найди", capabilities=["web-search"],
                        inputs={"provider": "exa"})
            self.assertTrue(dispatcher.execute(task)["ok"])
            self.assertEqual(quotas.store_for(settings).used("exa"), 1.0)
        finally:
            tmp.cleanup()

    def test_quota_accounted_from_lab100_output(self) -> None:
        from factory import quotas

        tmp, settings, dispatcher = self._make_isolated(FakeSourceAdapter)
        try:
            task = Task(goal="найди", capabilities=["web-search"])
            self.assertTrue(dispatcher.execute(task)["ok"])
            self.assertEqual(quotas.store_for(settings).used("local-browser"), 1.0)
        finally:
            tmp.cleanup()

    def test_no_provider_means_no_quota(self) -> None:
        from factory import quotas

        tmp, settings, dispatcher = self._make_isolated()
        try:
            task = Task(goal="найди", capabilities=["web-search"])
            self.assertTrue(dispatcher.execute(task)["ok"])
            self.assertEqual(quotas.store_for(settings).data, {})
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
