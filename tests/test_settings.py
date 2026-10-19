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

    def test_local_overlay_merges_deeply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config" / "settings.toml"
            path.parent.mkdir()
            path.write_text("""[providers.cline]
command = ""
model = "old"

[providers.workai]
enabled = false
""", encoding="utf-8")
            overlay = root / "state" / "config" / "settings.toml"
            overlay.parent.mkdir(parents=True)
            overlay.write_text("""[providers.cline]
command = "C:/some/local/cline.exe"

[providers.workai]
enabled = true
""", encoding="utf-8")
            settings = load(path)
            # локальный слой перекрывает команду
            self.assertEqual(settings.get("providers.cline.command"),
                             "C:/some/local/cline.exe")
            # ключи, не затронутые слоем, сохраняются
            self.assertEqual(settings.get("providers.cline.model"), "old")
            # включенность тоже перекрывается
            self.assertIs(settings.get("providers.workai.enabled"), True)


if __name__ == "__main__":
    unittest.main()
