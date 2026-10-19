"""Тесты каталога артефактов (§5.6) и диагностики адаптеров/интеграций."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import catalog
from factory.doctor import run_doctor, summarize
from factory.settings import Settings


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(self.settings_path, {})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_record(self, domain: str, name: str, goal: str = "цель") -> str:
        folder = self.root / "catalog" / domain / name
        folder.mkdir(parents=True)
        (folder / "task.json").write_text(
            json.dumps({"goal": goal}, ensure_ascii=False), encoding="utf-8")
        (folder / "result.json").write_text(
            json.dumps({"ok": True, "task": {"goal": goal}, "results": [1, 2]},
                       ensure_ascii=False), encoding="utf-8")
        (folder / "report.md").write_text(f"# {goal}\n", encoding="utf-8")
        return f"{domain}/{name}"

    def test_empty(self) -> None:
        data = catalog.build(self.settings)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["schema"], "invr.catalog/1")

    def test_entries_and_summary(self) -> None:
        self._write_record("news", "t1", goal="Новость дня")
        self._write_record("news", "t2", goal="Дайджест")
        self._write_record("weather", "t3", goal="Прогноз")
        data = catalog.build(self.settings)
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["domains"], {"news": 2, "weather": 1})
        names = {item["name"] for item in data["items"]}
        self.assertEqual(names, {"t1", "t2", "t3"})
        first = data["items"][0]
        self.assertTrue(first["has_report"])
        self.assertEqual(first["steps"], 2)
        self.assertTrue(first["ok"])

    def test_report_ok_and_traversal(self) -> None:
        self._write_record("news", "t1", goal="Новость")
        text = catalog.report(self.settings, "news/t1")
        self.assertIsNotNone(text)
        self.assertIn("Новость", text)
        # выход за пределы каталога запрещён
        self.assertIsNone(catalog.report(self.settings, "../config/settings.toml"))
        self.assertIsNone(catalog.report(self.settings, "news/missing"))


class DoctorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")
        tools = self.root / "tools"
        tools.mkdir()
        self.settings = Settings(
            self.settings_path, {"adapters": {"invr_root": str(tools)}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_doctor_shape(self) -> None:
        data = run_doctor(self.settings)
        self.assertEqual(set(data), {"adapters", "integrations"})
        self.assertTrue(data["adapters"])
        self.assertEqual(len(data["integrations"]), 4)
        for row in data["integrations"]:
            self.assertIn("integration", row)
            self.assertIn("available", row)
            self.assertIn("sink", row)

    def test_summarize(self) -> None:
        ready, total = summarize([{"available": True}, {"available": False}])
        self.assertEqual((ready, total), (1, 2))


if __name__ == "__main__":
    unittest.main()
