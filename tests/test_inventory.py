"""Тесты инвентаризации (этап 0): карта «что умеем»."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import inventory
from factory.settings import Settings


class InventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")

        # склад инструментов: registry/<tool>/<version>/ + шим
        self.tools = self.root / "scripts-tools"
        (self.tools / "registry" / "doc-extract" / "1.0.0").mkdir(parents=True)
        (self.tools / "registry" / "doc-extract" / "latest.txt").write_text("1.0.0", encoding="utf-8")
        (self.tools / "doc-extract.cmd").write_text("@echo off", encoding="utf-8")

        # пул ИИ: config/agents.toml (провайдеры + квоты)
        self.lab = self.root / "Lab100"
        (self.lab / "config").mkdir(parents=True)
        (self.lab / "config" / "agents.toml").write_text(
            'schema = "x"\n'
            '[provider.local-browser]\n'
            'kind = "search"\n'
            'capabilities = ["web-search", "scrape"]\n'
            'priority = 30\n'
            '[quota.local-browser]\n'
            'window = "day"\n'
            'limit = 100\n',
            encoding="utf-8",
        )

        self.region = self.root / "region-spoof"
        self.region.mkdir()
        (self.region / "config.json").write_text(
            json.dumps({"region": "US", "regions": [{"code": "US"}, {"code": "DE"}]}),
            encoding="utf-8",
        )

        self.deploy = self.root / "devstation-deploy"
        self.deploy.mkdir()
        (self.deploy / "project.json").write_text(
            json.dumps({"stages": [{"name": "install"}, {"name": "docker"}]}),
            encoding="utf-8",
        )

        self.settings = Settings(self.root / "config" / "settings.toml", {
            "adapters": {
                "invr_root": str(self.root / "scripts-tools"),
                "lab100_dir": str(self.root / "Lab100"),
                "region_repo": str(self.root / "region-spoof"),
                "deploy_dir": str(self.root / "devstation-deploy"),
            }
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_locate_uses_configured_paths(self) -> None:
        self.assertEqual(inventory.locate(self.settings, "Lab100"), self.root / "Lab100")
        self.assertEqual(inventory.locate(self.settings, "region-spoof"), self.root / "region-spoof")

    def test_locate_tools_store_from_home(self) -> None:
        # склад INVR (~/.devstation/tools) обнаруживается сам, без настройки пути
        home = self.root / "home"
        store = home / ".devstation" / "tools"
        (store / "registry" / "doc-extract" / "1.0.0").mkdir(parents=True)
        (store / "resolve-tools.ps1").write_text("#", encoding="utf-8")
        nested = self.root / "nested" / "factory"
        (nested / "config").mkdir(parents=True)
        (nested / "config" / "settings.toml").write_text("", encoding="utf-8")
        settings = Settings(nested / "config" / "settings.toml", {})
        with mock.patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            for name in ("INVR_TOOLS_ROOT", "DEVSTATION_TOOLS"):
                os.environ.pop(name, None)
            self.assertEqual(inventory.locate(settings, "scripts-tools"), store)
            data = inventory.build(settings)
        source = data["sources"]["scripts-tools"]
        self.assertTrue(source["available"])
        self.assertEqual(source["count"], 1)

    def test_absent_source_has_hint(self) -> None:
        # отсутствующий источник получает подсказку, как его подключить (4.4)
        iso = self.root / "iso" / "factory"
        (iso / "config").mkdir(parents=True)
        (iso / "config" / "settings.toml").write_text("", encoding="utf-8")
        settings = Settings(iso / "config" / "settings.toml", {})
        clean = {name: "" for spec in inventory.REPOS.values() for name in spec["env"]}
        with mock.patch.dict(os.environ, clean):
            for spec in inventory.REPOS.values():
                for name in spec["env"]:
                    os.environ.pop(name, None)
            data = inventory.build(settings)
        source = data["sources"]["region-spoof"]
        self.assertFalse(source["available"])
        self.assertIn("region_repo", source["hint"])

    def test_scanners(self) -> None:
        tools = inventory._scan_tools(self.root / "scripts-tools")
        self.assertEqual(tools["count"], 1)
        self.assertEqual(tools["tools"][0]["name"], "doc-extract")
        self.assertEqual(tools["tools"][0]["latest"], "1.0.0")
        self.assertIn("doc-extract", tools["shims"])

        lab = inventory._scan_lab100(self.root / "Lab100")
        self.assertEqual(lab["count"], 1)
        self.assertEqual(lab["providers"][0]["capabilities"], ["web-search", "scrape"])

        region = inventory._scan_region(self.root / "region-spoof")
        self.assertEqual(region["regions"], ["US", "DE"])

        deploy = inventory._scan_deploy(self.root / "devstation-deploy")
        self.assertEqual(deploy["stages"], ["install", "docker"])

    def test_build_and_save(self) -> None:
        data = inventory.build(self.settings)
        self.assertEqual(data["schema"], "invr.inventory/1")
        summary = data["summary"]
        self.assertEqual(summary["repos_present"], 4)
        self.assertEqual(summary["tools"], 1)
        self.assertEqual(summary["shims"], 1)
        self.assertEqual(summary["providers"], 1)
        self.assertEqual(summary["regions"], 2)
        self.assertEqual(summary["stages"], 2)
        self.assertIn("web-search", data["capabilities"])

        path = inventory.save(self.settings, data)
        self.assertTrue(path.exists())
        self.assertEqual(inventory.load_saved(self.settings)["summary"]["tools"], 1)


if __name__ == "__main__":
    unittest.main()
