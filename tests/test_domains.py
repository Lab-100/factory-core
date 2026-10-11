"""Тесты подвижных доменов."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.domains import DomainRegistry
from factory.task import Task

BASE = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.research]
title = "Исследование"
enabled = true
priority = 50
capabilities = ["web-search", "answer"]
keywords = ["найди", "обзор"]

[domain.infra]
title = "Инфра"
enabled = true
priority = 30
capabilities = ["answer"]
keywords = ["порт"]
"""

OVERLAY = """
schema = "invr.factory.profile/1"
name = "test"

[override.research]
priority = 99

[add.reporting]
title = "Отчётность"
enabled = true
priority = 45
capabilities = ["answer"]
keywords = ["отчёт"]
"""


class DomainRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        base = root / "domains.toml"
        overlay = root / "test.toml"
        base.write_text(BASE, encoding="utf-8")
        overlay.write_text(OVERLAY, encoding="utf-8")
        self.registry = DomainRegistry(base, [overlay])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_overlay_priority(self) -> None:
        self.assertEqual(self.registry.get("research").priority, 99)

    def test_added_domain(self) -> None:
        self.assertIn("reporting", self.registry.domains)
        self.assertEqual(self.registry.get("reporting").source, "add")

    def test_resolve_by_keyword(self) -> None:
        task = Task(goal="Сделай отчёт по теме")
        matched = self.registry.resolve(task)
        self.assertEqual(matched[0].name, "reporting")

    def test_resolve_by_capability(self) -> None:
        task = Task(goal="нейтральный запрос", capabilities=["web-search"])
        matched = self.registry.resolve(task)
        self.assertEqual(matched[0].name, "research")

    def test_explicit_and_disable(self) -> None:
        task = Task(goal="что угодно", domain="infra")
        self.assertEqual(self.registry.resolve(task)[0].name, "infra")
        self.registry.set_enabled("infra", False)
        self.assertEqual(self.registry.resolve(task), [])

    def test_persist_overlay_roundtrip(self) -> None:
        """Правка домена сохраняется в рантайм-профиль и переживает перезагрузку."""
        overlay = Path(self.tmp.name) / "_runtime.toml"
        self.registry.set_enabled("infra", False)
        self.registry.set_priority("research", 7)
        self.registry.persist_overlay(overlay)
        self.assertTrue(overlay.exists())
        reloaded = DomainRegistry(Path(self.tmp.name) / "domains.toml", [overlay])
        self.assertFalse(reloaded.get("infra").enabled)
        self.assertEqual(reloaded.get("research").priority, 7)


if __name__ == "__main__":
    unittest.main()
