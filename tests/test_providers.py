"""Тесты внешних ИИ-провайдеров: обнаружение, переключатель, адаптер, маршрут."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import providers
from factory.adapters.base import Adapter, StepResult
from factory.adapters.external_agent import ExternalAgentAdapter
from factory.dispatch import Dispatcher
from factory.domains import DomainRegistry
from factory.ledger import Ledger
from factory.settings import Settings

BASE = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.coding]
enabled = true
priority = 60
capabilities = ["code"]
"""


def _settings(tmp: Path, data: dict | None = None) -> Settings:
    (tmp / "config").mkdir(exist_ok=True)
    path = tmp / "config" / "settings.toml"
    path.write_text("", encoding="utf-8")
    return Settings(path, data or {})


class ProviderDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_catalog_names(self) -> None:
        for name in ("opencode", "claude-code", "cursor", "windsurf", "void",
                     "zen", "workai", "multitool"):
            self.assertIn(name, providers.names())

    def test_found_command_marks_available(self) -> None:
        settings = _settings(self.root)
        with mock.patch.object(providers.shutil, "which",
                               side_effect=lambda c: "/usr/bin/opencode" if c == "opencode" else None):
            by_name = {item["name"]: item for item in providers.discover(settings)}
        self.assertTrue(by_name["opencode"]["available"])
        self.assertEqual(by_name["opencode"]["command"], "/usr/bin/opencode")
        self.assertFalse(by_name["zen"]["available"])

    def test_enabled_switch_persists(self) -> None:
        from factory.settings import load

        settings = _settings(self.root)
        self.assertFalse(settings.get("providers.opencode.enabled", False))
        self.assertTrue(providers.set_enabled(settings, "opencode", True))
        self.assertTrue(settings.get("providers.opencode.enabled"))
        # перезагрузка с диска сохраняет переключатель
        reread = load(settings.path)
        self.assertTrue(reread.get("providers.opencode.enabled", False))

    def test_unknown_provider_rejected(self) -> None:
        settings = _settings(self.root)
        self.assertFalse(providers.set_enabled(settings, "нет-такого", True))

    def test_configured_command_overrides(self) -> None:
        settings = _settings(self.root, {"providers": {"zen": {"command": "zen-cli"}}})
        with mock.patch.object(providers.shutil, "which",
                               side_effect=lambda c: "/opt/zen-cli" if c == "zen-cli" else None):
            info = {i["name"]: i for i in providers.discover(settings)}["zen"]
            command = providers.run_command(settings, "zen")
        self.assertTrue(info["available"])
        self.assertEqual(info["command"], "/opt/zen-cli")
        self.assertEqual(command[0], "/opt/zen-cli")


class ExternalAgentAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unavailable_without_enabled(self) -> None:
        settings = _settings(self.root)
        adapter = ExternalAgentAdapter(settings)
        self.assertFalse(adapter.available())
        result = adapter.run("chat", {"prompt": "привет"})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")

    def test_dry_run_with_enabled(self) -> None:
        settings = _settings(self.root, {"providers": {"workai": {"enabled": True,
                                                                 "command": "workai"}}})
        adapter = ExternalAgentAdapter(settings)
        self.assertTrue(adapter.available())
        result = adapter.run("chat", {"prompt": "привет"}, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.output["provider"], "workai")

    def test_unknown_provider_param(self) -> None:
        settings = _settings(self.root, {"providers": {"workai": {"enabled": True}}})
        result = ExternalAgentAdapter(settings).run("chat", {"provider": "cursor"},
                                                    dry_run=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")

    def test_health_lists_enabled(self) -> None:
        settings = _settings(self.root, {"providers": {"void": {"enabled": True}}})
        health = ExternalAgentAdapter(settings).health()
        self.assertTrue(health["available"])
        self.assertIn("void", health["enabled"])


class FakeExternal(Adapter):
    name = "external_agent"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"provider": params.get("provider")})


class FakeLocal(Adapter):
    name = "llm_local"

    def run(self, action, params, dry_run=False):  # noqa: ANN001
        return StepResult(True, "ok", self.name, action, {"echo": params.get("prompt")})


class RoutingTest(unittest.TestCase):
    def _dispatcher(self, settings: Settings):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        base = root / "domains.toml"
        base.write_text(BASE, encoding="utf-8")
        registry = DomainRegistry(base, [])
        adapters = {"external_agent": FakeExternal(settings), "llm_local": FakeLocal(settings)}
        dispatcher = Dispatcher(settings, registry, Ledger(root / "ledger.jsonl"),
                                adapters=adapters, integrations={})
        return tmp, dispatcher

    def test_routing_override_sends_to_external(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            settings = _settings(Path(tmp.name))
            settings.set("routing.code", "external_agent:chat")
            tmp2, dispatcher = self._dispatcher(settings)
            try:
                from factory.task import Task

                task = Task(goal="напиши код", capabilities=["code"],
                            inputs={"provider": "opencode"})
                matched, steps = dispatcher.plan(task)
                self.assertEqual(steps[0].adapter, "external_agent")
                self.assertEqual(steps[0].action, "chat")
                self.assertEqual(steps[0].params.get("provider"), "opencode")
            finally:
                tmp2.cleanup()
        finally:
            tmp.cleanup()

    def test_auto_route_off_keeps_builtin(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            settings = _settings(Path(tmp.name))
            tmp2, dispatcher = self._dispatcher(settings)
            try:
                from factory.task import Task

                matched, steps = dispatcher.plan(Task(goal="напиши код", capabilities=["code"]))
                self.assertEqual(steps[0].adapter, "llm_local")
            finally:
                tmp2.cleanup()
        finally:
            tmp.cleanup()

    def test_auto_route_on_with_enabled_provider(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            settings = _settings(Path(tmp.name),
                                 {"providers": {"opencode": {"enabled": True},
                                                "auto_route": True}})
            tmp2, dispatcher = self._dispatcher(settings)
            try:
                from factory.task import Task

                matched, steps = dispatcher.plan(Task(goal="напиши код", capabilities=["code"]))
                self.assertEqual(steps[0].adapter, "external_agent")
            finally:
                tmp2.cleanup()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
