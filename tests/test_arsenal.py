"""Тесты арсенала: единый каталог элементов (repo/tool/agent/service/env)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import arsenal
from factory.settings import Settings
from factory.ui.cli import build_parser


class ArsenalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.toml").write_text("", encoding="utf-8")

        # склад инструментов
        self.tools = self.root / "scripts-tools"
        (self.tools / "registry" / "doc-extract" / "1.0.0").mkdir(parents=True)
        (self.tools / "registry" / "doc-extract" / "latest.txt").write_text("1.0.0", encoding="utf-8")
        (self.tools / "doc-extract.cmd").write_text("@echo off", encoding="utf-8")
        # Docker-инструмент (должен попадать в исключённые)
        (self.tools / "registry" / "gordon" / "0.1.0").mkdir(parents=True)
        (self.tools / "registry" / "gordon" / "latest.txt").write_text("0.1.0", encoding="utf-8")

        # пул ИИ
        self.lab = self.root / "Lab100"
        (self.lab / "config").mkdir(parents=True)
        (self.lab / "config" / "agents.toml").write_text(
            'schema = "x"\n[provider.local-browser]\nkind = "search"\n'
            'capabilities = ["web-search"]\npriority = 30\n',
            encoding="utf-8",
        )

        (self.root / "region-spoof").mkdir()
        (self.root / "region-spoof" / "config.json").write_text(
            json.dumps({"region": "US", "regions": [{"code": "US"}]}), encoding="utf-8")

        self.deploy = self.root / "devstation-deploy"
        self.deploy.mkdir()
        (self.deploy / "project.json").write_text(json.dumps({"stages": ["install"]}),
                                                  encoding="utf-8")

        # окружение OpenCode
        self.oc = self.root / "opencode-config"
        self.oc.mkdir()
        (self.oc / "AGENTS.md").write_text("rules", encoding="utf-8")
        (self.oc / "opencode.json").write_text("{}", encoding="utf-8")

        self.settings = Settings(self.root / "config" / "settings.toml", {
            "adapters": {
                "invr_root": str(self.tools),
                "lab100_dir": str(self.lab),
                "region_repo": str(self.root / "region-spoof"),
                "deploy_dir": str(self.deploy),
            },
            "opencode": {"config_dir": str(self.oc)},
            "services": {
                "my-service": {"title": "Своя служба", "kind": "command",
                               "command": ["definitely-missing-binary"], "owner": "factory"},
            },
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _by_id(self, data: dict) -> dict:
        return {e["id"]: e for e in data["elements"]}

    def test_build_contains_all_classes(self) -> None:
        data = arsenal.build(self.settings)
        self.assertEqual(data["schema"], "invr.arsenal/1")
        classes = {e["class"] for e in data["elements"]}
        self.assertEqual(classes, {"repo", "tool", "agent", "service", "env"})
        by_id = self._by_id(data)
        self.assertIn("tool:doc-extract", by_id)
        self.assertIn("agent:local-browser", by_id)
        self.assertIn("repo:scripts-tools", by_id)
        self.assertIn("env:AGENTS.md", by_id)
        self.assertIn("service:ollama", by_id)
        self.assertIn("service:my-service", by_id)

    def test_tool_meta_and_shim(self) -> None:
        data = arsenal.build(self.settings)
        tool = self._by_id(data)["tool:doc-extract"]
        self.assertEqual(tool["meta"]["latest"], "1.0.0")
        self.assertTrue(tool["meta"]["shim"].endswith("doc-extract.cmd"))
        self.assertTrue(tool["available"])

    def test_env_availability(self) -> None:
        data = arsenal.build(self.settings)
        by_id = self._by_id(data)
        self.assertTrue(by_id["env:AGENTS.md"]["available"])
        self.assertFalse(by_id["env:skills"]["available"])

    def test_docker_services_excluded(self) -> None:
        data = arsenal.build(self.settings)
        by_id = self._by_id(data)
        docker = by_id["service:docker"]
        self.assertTrue(docker["meta"]["docker"])
        self.assertFalse(docker["available"])
        self.assertFalse(docker["enabled"])
        self.assertGreaterEqual(data["summary"]["docker_excluded"], 3)

    def test_docker_tools_excluded(self) -> None:
        data = arsenal.build(self.settings)
        by_id = self._by_id(data)
        gordon = by_id["tool:gordon"]
        self.assertTrue(gordon["meta"]["docker"])
        self.assertFalse(gordon["enabled"])
        self.assertFalse(by_id["tool:doc-extract"]["meta"]["docker"])
        # 3 службы (docker/gordon/model-runner) + инструмент gordon
        self.assertGreaterEqual(data["summary"]["docker_excluded"], 4)

    def test_watchdog_path_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"INVR_TOOLS_ROOT": str(self.tools)}):
            data = arsenal.build(self.settings)
        watchdog = self._by_id(data)["service:mcp-watchdog"]
        rel = watchdog["path"].replace("\\", "/")
        self.assertTrue(rel.endswith("mcp-watchdog/state/current.json"))

    def test_summary_counts(self) -> None:
        data = arsenal.build(self.settings)
        summary = data["summary"]
        self.assertEqual(summary["total"], len(data["elements"]))
        self.assertEqual(summary["by_class"]["tool"], 2)  # doc-extract + gordon
        self.assertEqual(summary["by_class"]["agent"], 1)
        self.assertGreaterEqual(summary["by_class"]["service"], 9)  # 8 defaults + 1 своя

    def test_save_and_load(self) -> None:
        data = arsenal.build(self.settings)
        path = arsenal.save(self.settings, data)
        self.assertTrue(path.exists())
        loaded = arsenal.load_saved(self.settings)
        self.assertEqual(loaded["schema"], "invr.arsenal/1")
        self.assertEqual(loaded["summary"]["total"], data["summary"]["total"])

    def test_cli_parsers_registered(self) -> None:
        parser = build_parser("ru")
        for command in ("arsenal", "logs", "supervise"):
            args = parser.parse_args([command])
            self.assertTrue(callable(getattr(args, "func", None)))


if __name__ == "__main__":
    unittest.main()
