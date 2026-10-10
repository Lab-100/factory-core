"""Тесты квот исполнителей Lab100 (этап 4)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory import quotas
from factory.settings import Settings


class QuotasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")

        lab = self.root / "Lab100"
        (lab / "config").mkdir(parents=True)
        (lab / "config" / "agents.toml").write_text(
            '[quota.exa]\n'
            'window = "month"\n'
            'limit = 100\n'
            '[quota.free]\n'
            'window = "forever"\n',
            encoding="utf-8",
        )
        self.settings = Settings(self.settings_path, {"adapters": {"lab100_dir": str(lab)}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_limits(self) -> None:
        data = quotas.build(self.settings)
        self.assertTrue(data["available"])
        exa = data["providers"]["exa"]
        self.assertEqual(exa["limit"], 100)
        self.assertEqual(exa["remaining"], 100.0)
        self.assertFalse(exa["unlimited"])
        free = data["providers"]["free"]
        self.assertTrue(free["unlimited"])
        self.assertIsNone(free["remaining"])

    def test_record_and_remaining(self) -> None:
        quotas.record(self.settings, "exa", 5)
        data = quotas.build(self.settings)
        exa = data["providers"]["exa"]
        self.assertEqual(exa["used"], 5.0)
        self.assertEqual(exa["remaining"], 95.0)
        self.assertEqual(exa["percent"], 95.0)

    def test_reset(self) -> None:
        quotas.record(self.settings, "exa", 7)
        quotas.reset(self.settings, "exa")
        self.assertEqual(quotas.build(self.settings)["providers"]["exa"]["used"], 0.0)

    def test_build_shape(self) -> None:
        data = quotas.build(self.settings)
        self.assertEqual(data["schema"], "invr.quotas/1")
        self.assertIn("providers", data)


if __name__ == "__main__":
    unittest.main()
