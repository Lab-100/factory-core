"""Тесты интеграций: backup-util (ChangeId) и orch-metrics (этап 3)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.integrations import build_integrations
from factory.integrations.backup import BackupIntegration
from factory.settings import Settings


class IntegrationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")
        # пустой склад инструментов: шимы не найдены → локальный путь
        tools = self.root / "tools"
        tools.mkdir()
        self.settings = Settings(self.settings_path, {"adapters": {"invr_root": str(tools)}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build(self) -> None:
        integrations = build_integrations(self.settings)
        self.assertEqual(set(integrations), {"backup", "metrics", "infra"})

    def test_backup_local_fallback(self) -> None:
        backup = BackupIntegration(self.settings)
        self.assertFalse(backup.available())
        result = backup.record(["catalog/report.md"], reason="тест", task="t1")
        self.assertEqual(result["source"], "local")
        self.assertTrue(result["change_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["paths"], ["catalog/report.md"])

    def test_backup_parse_change(self) -> None:
        self.assertEqual(BackupIntegration._parse_change('лог\n{"change_id": "abc123"}'), "abc123")
        self.assertEqual(BackupIntegration._parse_change('{"changeId": "z9"}'), "z9")
        self.assertIsNone(BackupIntegration._parse_change("нет json"))

    def test_metrics_local_sink(self) -> None:
        integrations = build_integrations(self.settings)
        result = integrations["metrics"].emit({"kind": "task", "task": "t1", "ok": True})
        self.assertEqual(result["sink"], "local")
        metrics_file = self.root / "state" / "metrics.jsonl"
        self.assertTrue(metrics_file.exists())
        record = json.loads(metrics_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["task"], "t1")
        self.assertIn("ts", record)

    def test_infra_local_sink(self) -> None:
        integrations = build_integrations(self.settings)
        result = integrations["infra"].emit({"kind": "flow", "task": "t1", "tools": ["doc-extract"]})
        self.assertEqual(result["sink"], "local")
        self.assertTrue(result["ok"])
        path = self.root / "state" / "infra.jsonl"
        self.assertTrue(path.exists())
        self.assertIn("doc-extract", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
