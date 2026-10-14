# DAG режим — простейшие тесты (базовые кейсы)
# проверка корректности работы графа с зависимостями, параллелизмом, условиями и остановкой
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
capabilities = ["web-search", "scrape", "answer"]
tools = []
region = "auto"
llm = []
"""

SETTINGS = {"automation": {"mode": "auto", "semi": {"checkpoints": []}}}


class FakeWeb(Adapter):
    name = "lab100"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"echo": params.get("query")})


class FakeLlm(Adapter):
    name = "llm_local"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"echo": params.get("prompt")})


class FakeScrape(Adapter):
    name = "lab100"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"echo": params.get("query"), "data": True})


class DagTest(unittest.TestCase):
    def _make(self, automation: str = "auto"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config").mkdir()
        (root / "domains.toml").write_text(BASE, encoding="utf-8")
        registry = DomainRegistry(root / "domains.toml", [])
        settings = Settings(root / "config" / "settings.toml", dict(SETTINGS))
        settings.set("automation.mode", automation)
        ledger = Ledger(root / "ledger.jsonl")
        adapters = {
            "lab100": FakeWeb(settings),
            "llm_local": FakeLlm(settings),
        }
        dispatcher = Dispatcher(settings, registry, ledger, adapters=adapters, integrations={})
        return tmp, dispatcher, ledger, settings, root

    def test_dag_sequential_chain_via_flag(self):
        tmp, dispatcher, _, _, _ = self._make()
        try:
            task = Task(goal="find", capabilities=["web-search", "scrape", "answer"],
                        constraints={"dag": True})
            res = dispatcher.execute(task, dry_run=False)
            self.assertTrue(res["ok"])
            # режим dag
            self.assertEqual(res["mode"], "dag")
            # каждый следующий зависит от предыдущего
            deps = [s.get("depends_on") for s in res["steps"]]
            self.assertIsNone(deps[0])
            self.assertEqual(deps[1], ["n0"])
            self.assertEqual(deps[2], ["n1"])
        finally:
            tmp.cleanup()

    def test_dag_explicit_graph_conditions(self):
        tmp, dispatcher, _, _, _ = self._make()
        try:
            task = Task(goal="g", constraints={
                "dag": [
                    {"id": "s1", "capability": "web-search"},
                    {"id": "s2", "capability": "scrape", "depends_on": ["s1"], "when": "s1.ok"},
                    {"id": "s3", "capability": "answer", "depends_on": ["s1"], "when": "not s2.ok"},
                ]
            })
            res = dispatcher.execute(task, dry_run=False)
            self.assertTrue(res["ok"])
            statuses = [r["status"] for r in res["results"]]
            self.assertEqual(statuses[0], "ok")
            self.assertEqual(statuses[1], "ok")
            self.assertEqual(statuses[2], "skipped")  # s2.ok => not s2.ok ложно
        finally:
            tmp.cleanup()

    def test_dag_parent_failed_skips_branch(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "config").mkdir()
            (root / "domains.toml").write_text(BASE, encoding="utf-8")
            registry = DomainRegistry(root / "domains.toml", [])
            settings = Settings(root / "config" / "settings.toml", dict(SETTINGS))
            ledger = Ledger(root / "ledger.jsonl")

            class Bad(FakeScrape):
                def run(self, action, params, dry_run=False):  # noqa: ANN001
                    return StepResult(False, "error", self.name, action, error="fail")

            adapters = {
                "lab100": Bad(settings),
                "llm_local": FakeLlm(settings),
            }
            dispatcher = Dispatcher(settings, registry, ledger, adapters=adapters, integrations={})
            task = Task(goal="g", constraints={
                "dag": [
                    {"id": "s1", "capability": "web-search"},
                    {"id": "s2", "capability": "scrape", "depends_on": ["s1"]},
                    {"id": "s3", "capability": "answer", "depends_on": ["s2"]},
                ]
            })
            res = dispatcher.execute(task, dry_run=False)
            # s1 упал -> s2 пропущен по предку -> s3 пропущен каскадом
            self.assertEqual(res["results"][0]["status"], "error")
            self.assertEqual(res["results"][1]["status"], "skipped")
            self.assertEqual(res["results"][2]["status"], "skipped")
            self.assertFalse(res["ok"])
        finally:
            tmp.cleanup()

    def test_dag_parallel_level(self):
        tmp, dispatcher, _, _, _ = self._make()
        try:
            task = Task(goal="g", constraints={
                "dag": [
                    {"id": "root", "capability": "web-search"},
                    {"id": "a", "capability": "scrape", "depends_on": ["root"]},
                    {"id": "b", "capability": "answer", "depends_on": ["root"]},
                ]
            })
            res = dispatcher.execute(task, dry_run=False)
            self.assertTrue(res["ok"])
            # a и b на одном уровне (после root)
            self.assertEqual(len(res["steps"]), 3)
        finally:
            tmp.cleanup()

    def test_dag_cycle_detected(self):
        tmp, dispatcher, _, _, _ = self._make()
        try:
            task = Task(goal="g", constraints={
                "dag": [
                    {"id": "s1", "capability": "web-search", "depends_on": ["s2"]},
                    {"id": "s2", "capability": "scrape", "depends_on": ["s1"]},
                ]
            })
            with self.assertRaises(ValueError):
                dispatcher.plan(task)
        finally:
            tmp.cleanup()

    def test_dag_semi_approval(self):
        tmp, dispatcher, _, _, _ = self._make("semi")
        try:
            task = Task(goal="g", capabilities=["scrape"], constraints={"dag": True})
            res = dispatcher.execute(task, approve=None)
            self.assertTrue(res["stopped"])
            self.assertFalse(res["ok"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
