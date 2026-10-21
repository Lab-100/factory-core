"""Тесты выборки возможностей провайдеров (capabilities) — без внешних вызовов."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class FakeSettings:
    """Минимальные настройки для рендера/построения capabilities."""

    path = ""

    def __init__(self, config_dir: str):
        self._data = {
            "opencode": {"config_dir": config_dir},
            "providers.cline.models": [
                "cline|cline-free/mimo-v2.6-flash",
                "cline|cline-free/solar-mini4",
            ],
        }

    def get(self, key, default=None):
        # как у настоящих настроек фабрики: плоский ключ с точками
        # («opencode.config_dir») разворачивается во вложенный словарь
        if key == "opencode.config_dir":
            return self._data["opencode"]["config_dir"]
        return self._data.get(key, default)


class CapabilitiesTest(unittest.TestCase):
    def test_render_text_has_sections(self) -> None:
        from factory import capabilities

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "opencode"
            (cfg / "skills").mkdir(parents=True)
            (cfg / "skills" / "firecrawl-search").mkdir()
            (cfg / "skills" / "firecrawl-search" / "SKILL.md").write_text(
                "description: поиск в интернете\n---\n", encoding="utf-8")
            (cfg / "plugins").mkdir()
            (cfg / "plugins" / "status-banner.js").write_text("//x", encoding="utf-8")
            (cfg / "opencode.json").write_text(
                '{"mcp": {"time": {"command": ["uvx", "mcp-server-time"]}}}',
                encoding="utf-8")
            data = capabilities.build(FakeSettings(str(cfg)))
            text = capabilities.render_text(data)
        self.assertIn("MCP-серверы", text)
        self.assertIn("Скиллы", text)
        self.assertIn("Плагины", text)
        self.assertIn("Субмодели", text)
        self.assertIn("Серверы", text)

    def test_counts(self) -> None:
        from factory import capabilities

        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "opencode"
            (cfg / "skills").mkdir(parents=True)
            (cfg / "skills" / "a").mkdir()
            (cfg / "skills" / "b").mkdir()
            (cfg / "plugins").mkdir()
            (cfg / "plugins" / "p1.js").write_text("", encoding="utf-8")
            (cfg / "opencode.json").write_text(
                '{"mcp": {"x": {"command": ["x"], "enabled": true}}}',
                encoding="utf-8")
            data = capabilities.build(FakeSettings(str(cfg)))
            self.assertGreaterEqual(data["summary"]["skills"], 2)
            self.assertGreaterEqual(data["summary"]["plugins"], 1)
            self.assertGreaterEqual(data["summary"]["mcp"], 1)


if __name__ == "__main__":
    unittest.main()