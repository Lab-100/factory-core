"""Опциональный живой прогон на реальной установке (включается FACTORY_LIVE=1).

В CI (где нет исходных репозиториев и внешних сервисов) тест пропускается.
Локально, при установленной фабрике с репозиториями, он проверяет то, что не
покрывают изолированные тесты: сборку настоящего контекста (настройки/домены/
журнал), обнаружение источников инвентаря и сквозной dry-run через реальные
адаптеры. Реальных внешних вызовов не делает — для этого снимите `dry_run`.
"""
from __future__ import annotations

import os
import unittest

from factory import inventory
from factory.context import build_context
from factory.dispatch import Dispatcher
from factory.task import Task

LIVE = os.environ.get("FACTORY_LIVE") == "1"


@unittest.skipUnless(LIVE, "живой прогон: задайте FACTORY_LIVE=1 (нужна установка с репозиториями)")
class LiveSmokeTest(unittest.TestCase):
    def test_context_and_inventory(self) -> None:
        root, settings, _registry, _ledger = build_context()
        self.assertTrue((root / "config" / "settings.toml").exists())
        data = inventory.build(settings)
        self.assertEqual(data["schema"], "invr.inventory/1")
        self.assertIn("sources", data)
        self.assertIn("summary", data)

    def test_real_dry_run(self) -> None:
        _root, settings, registry, ledger = build_context()
        dispatcher = Dispatcher(settings, registry, ledger)
        task = Task(goal="живой прогон: найди данные", capabilities=["web-search"])
        result = dispatcher.execute(task, dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertNotEqual(result["results"], [])


if __name__ == "__main__":
    unittest.main()
