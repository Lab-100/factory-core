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
tools = ["doc-extract"]
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


class UnavailableAdapter(Adapter):
    """Недоступный адаптер: dry-run должен пройти, реальный вызов — нет."""

    name = "region"

    def available(self) -> bool:
        return False

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        if dry_run:
            return StepResult(True, "simulated", self.name, action, {"note": "dry-run"})
        raise AssertionError("недоступный адаптер не должен вызываться без dry-run")


class DispatcherTest(unittest.TestCase):
    def _make(self, automation: str = "auto"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config").mkdir()
        base = root / "domains.toml"
        base.write_text(BASE, encoding="utf-8")
        registry = DomainRegistry(base, [])
        settings = Settings(root / "config" / "settings.toml", dict(SETTINGS))
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
            self.assertEqual(len(ledger.tail(10)), 5)  # start + 2 шага + каталог + done
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

    def test_dry_run_simulates_unavailable_adapters(self) -> None:
        """dry-run не зависит от наличия репозиториев/инструментов."""
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "config").mkdir()
            (root / "domains.toml").write_text(BASE, encoding="utf-8")
            registry = DomainRegistry(root / "domains.toml", [])
            settings = Settings(root / "config" / "settings.toml", dict(SETTINGS))
            settings.set("automation.mode", "auto")
            adapters = {"lab100": FakeAdapter(settings), "llm_local": FakeLlm(settings),
                        "region": UnavailableAdapter(settings)}
            dispatcher = Dispatcher(settings, registry, Ledger(root / "ledger.jsonl"),
                                    adapters=adapters, integrations={})
            task = Task(goal="найди", capabilities=["web-search"],
                        constraints={"allow_region_switch": True})
            result = dispatcher.execute(task, dry_run=True)
            self.assertTrue(result["ok"])
            statuses = [r["status"] for r in result["results"]]
            self.assertNotIn("unavailable", statuses)
            region = [r for r in result["results"] if r["adapter"] == "region"]
            self.assertEqual(region[0]["status"], "simulated")
        finally:
            tmp.cleanup()

    def test_tool_steps_from_task(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="извлеки текст", capabilities=["answer"],
                        tools=["doc-extract", {"name": "browsertool", "args": ["--url", "x"]}])
            _, steps = dispatcher.plan(task)
            tools = {s.params.get("tool"): s for s in steps if s.adapter == "invr"}
            self.assertEqual(set(tools), {"doc-extract", "browsertool"})
            self.assertEqual(tools["browsertool"].params["args"], ["--url", "x"])
            self.assertEqual(tools["doc-extract"].action, "run")
            self.assertEqual(tools["doc-extract"].capability, "tool:doc-extract")
        finally:
            tmp.cleanup()

    def test_domain_tools_only_on_opt_in(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            plain = Task(goal="извлеки", capabilities=["answer"])
            _, steps = dispatcher.plan(plain)
            self.assertFalse(any(s.adapter == "invr" for s in steps))

            opted = Task(goal="извлеки", capabilities=["answer"],
                         constraints={"use_domain_tools": True})
            _, steps2 = dispatcher.plan(opted)
            self.assertTrue(any(s.adapter == "invr" and s.params.get("tool") == "doc-extract"
                                for s in steps2))
        finally:
            tmp.cleanup()

    def test_catalog_artifacts_written(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди данные", capabilities=["answer"])
            result = dispatcher.execute(task, dry_run=False)
            self.assertIn("catalog", result)
            target = Path(tmp.name) / result["catalog"]
            self.assertTrue((target / "task.json").exists())
            self.assertTrue((target / "result.json").exists())
            self.assertTrue((target / "report.md").exists())
        finally:
            tmp.cleanup()

    def test_catalog_logged_to_ledger(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди данные", capabilities=["answer"])
            result = dispatcher.execute(task, dry_run=False)
            events = [e for e in dispatcher.ledger.tail(30) if e.get("kind") == "catalog"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["catalog"], result["catalog"])
        finally:
            tmp.cleanup()

    def test_region_retry_on_geoblock(self) -> None:
        """Гео-блок Lab100 → смена региона → повтор шага (§4.5)."""
        tmp, dispatcher, ledger = self._make("auto")
        try:
            calls = {"lab": 0, "switch": 0}

            class BlockingLab(Adapter):
                name = "lab100"

                def run(self, action, params, dry_run=False):  # noqa: ANN001
                    calls["lab"] += 1
                    if params.get("region_retry"):
                        return StepResult(True, "ok", self.name, action, {"stdout": "ok"})
                    return StepResult(False, "error", self.name, action,
                                      {"stderr": "403 Forbidden"}, error="403 Forbidden")

            class FakeRegion(Adapter):
                name = "region"

                def available(self) -> bool:
                    return True

                def run(self, action, params, dry_run=False):  # noqa: ANN001
                    calls["switch"] += 1
                    return StepResult(True, "ok", self.name, action,
                                      {"region": params.get("region")})

            dispatcher.adapters = {
                "lab100": BlockingLab(dispatcher.settings),
                "llm_local": FakeLlm(dispatcher.settings),
                "region": FakeRegion(dispatcher.settings),
            }
            task = Task(goal="найди", capabilities=["web-search"],
                        constraints={"allow_region_switch": True})
            result = dispatcher.execute(task, dry_run=False)
            self.assertTrue(result["ok"])
            self.assertEqual(calls["lab"], 2)       # упал → повтор после смены региона
            self.assertEqual(calls["switch"], 2)    # плановый шаг + реактивный
            retries = [e for e in ledger.tail(30) if e.get("kind") == "region-retry"]
            self.assertEqual(len(retries), 1)
            self.assertTrue(retries[0]["ok"])
        finally:
            tmp.cleanup()

    def test_no_region_retry_without_permission(self) -> None:
        """Без allow_region_switch гео-блок не приводит к повтору (§4.5)."""
        tmp, dispatcher, ledger = self._make("auto")
        try:
            calls = {"lab": 0}

            class BlockingLab(Adapter):
                name = "lab100"

                def run(self, action, params, dry_run=False):  # noqa: ANN001
                    calls["lab"] += 1
                    return StepResult(False, "error", self.name, action,
                                      error="403 Forbidden")

            dispatcher.adapters = {"lab100": BlockingLab(dispatcher.settings)}
            task = Task(goal="найди", capabilities=["web-search"])
            result = dispatcher.execute(task, dry_run=False)
            self.assertFalse(result["ok"])
            self.assertEqual(calls["lab"], 1)
            self.assertFalse(any(e.get("kind") == "region-retry" for e in ledger.tail(30)))
        finally:
            tmp.cleanup()

    def test_acceptance_recorded(self) -> None:
        """Критерии приёмки попадают в итог и в отчёт (§4.5)."""
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди данные", capabilities=["answer"],
                        acceptance=["источники указаны", "объём достаточен"])
            result = dispatcher.execute(task, dry_run=False)
            self.assertEqual(result["acceptance"], ["источники указаны", "объём достаточен"])
            report = (Path(tmp.name) / result["catalog"] / "report.md").read_text(encoding="utf-8")
            self.assertIn("Критерии приёмки", report)
            self.assertIn("источники указаны", report)
        finally:
            tmp.cleanup()

    def test_vision_step_carries_images(self) -> None:
        """Компетенция vision передаёт пути к изображениям в локальную LLM (§4.5)."""
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="опиши картинку", capabilities=["vision"],
                        inputs={"image": "C:/tmp/pic.png", "model": "llava"})
            _, steps = dispatcher.plan(task)
            vision = [s for s in steps if s.capability == "vision"]
            self.assertEqual(len(vision), 1)
            self.assertEqual(vision[0].adapter, "llm_local")
            self.assertEqual(vision[0].params["images"], ["C:/tmp/pic.png"])
            self.assertEqual(vision[0].params["model"], "llava")
        finally:
            tmp.cleanup()

    def test_catalog_explicit_path_used(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди", capabilities=["answer"],
                        outputs={"catalog": "catalog/custom/run-1"})
            result = dispatcher.execute(task, dry_run=False)
            self.assertEqual(result["catalog"], "catalog/custom/run-1")
            self.assertTrue((Path(tmp.name) / "catalog" / "custom" / "run-1" / "report.md").exists())
        finally:
            tmp.cleanup()

    def test_dry_run_writes_no_catalog(self) -> None:
        tmp, dispatcher, _ = self._make("auto")
        try:
            task = Task(goal="найди", capabilities=["answer"])
            result = dispatcher.execute(task, dry_run=True)
            self.assertNotIn("catalog", result)
            self.assertFalse((Path(tmp.name) / "catalog").exists())
        finally:
            tmp.cleanup()

    def test_quota_logged_to_ledger(self) -> None:
        tmp, _, dispatcher = self._make_isolated()
        try:
            task = Task(goal="найди", capabilities=["web-search"], inputs={"provider": "exa"})
            dispatcher.execute(task)
            quota_events = [e for e in dispatcher.ledger.tail(20) if e.get("kind") == "quota"]
            self.assertEqual(len(quota_events), 1)
            self.assertEqual(quota_events[0]["provider"], "exa")
            self.assertEqual(quota_events[0]["used"], 1)
        finally:
            tmp.cleanup()

    def test_metrics_and_infra_reach_ledger(self) -> None:
        """Интеграции без шимов пишут локально и попадают в единый журнал (§8.4)."""
        from factory.integrations import build_integrations

        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "config").mkdir()
            (root / "tools").mkdir()
            domains_file = root / "config" / "domains.toml"
            domains_file.write_text(
                'schema = "invr.factory.domains/1"\nmatch_by = "auto"\n'
                '[domain.research]\nenabled = true\npriority = 50\n'
                'capabilities = ["web-search"]\n',
                encoding="utf-8",
            )
            registry = DomainRegistry(domains_file, [])
            settings = Settings(root / "config" / "settings.toml", {
                "automation": {"mode": "auto", "semi": {"checkpoints": []}},
                "adapters": {"invr_root": str(root / "tools")},
            })
            ledger = Ledger(root / "ledger.jsonl")
            dispatcher = Dispatcher(settings, registry, ledger,
                                    adapters={"lab100": FakeAdapter(settings)},
                                    integrations=build_integrations(settings))
            dispatcher.execute(Task(goal="найди", capabilities=["web-search"]))
            kinds = [e.get("kind") for e in ledger.tail(30)]
            self.assertIn("metrics", kinds)
            self.assertTrue((root / "state" / "metrics.jsonl").exists())
            self.assertTrue((root / "state" / "infra.jsonl").exists())
        finally:
            tmp.cleanup()

    def _make_isolated(self, adapter_cls=FakeAdapter):
        """Диспетчер в изолированном каталоге (проверка записи квот и каталога)."""
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
