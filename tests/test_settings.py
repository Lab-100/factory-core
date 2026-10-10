"""Тесты настроек."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.settings import load

SAMPLE = """
schema = "invr.factory.settings/1"
active_profile = "default"

[ui]
mode = "all"

[automation]
mode = "auto"

[automation.semi]
checkpoints = ["scrape", "write"]
"""


class SettingsTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.toml"
            path.write_text(SAMPLE, encoding="utf-8")
            settings = load(path)
            self.assertEqual(settings.automation, "auto")
            self.assertEqual(settings.ui, "all")
            self.assertEqual(settings.checkpoints, ["scrape", "write"])

            settings.set("automation.mode", "semi")
            settings.set("ui.mode", "web")
            settings.save()

            reloaded = load(path)
            self.assertEqual(reloaded.automation, "semi")
            self.assertEqual(reloaded.ui, "web")
            self.assertEqual(reloaded.checkpoints, ["scrape", "write"])

    def test_coerce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.toml"
            path.write_text(SAMPLE, encoding="utf-8")
            settings = load(path)
            settings.set("healthcheck", "false")
            self.assertIs(settings.get("healthcheck"), False)
            settings.set("ui.web.port", "9999")
            self.assertEqual(settings.get("ui.web.port"), 9999)


if __name__ == "__main__":
    unittest.main()
