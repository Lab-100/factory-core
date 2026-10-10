"""Тесты адаптеров: соответствие командам внешних репозиториев."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.adapters.lab100 import Lab100Adapter
from factory.settings import Settings


class Lab100CommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        settings_path = root / "config" / "settings.toml"
        settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(settings_path, {"adapters": {"lab100_python": "python"}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_research_maps_to_ask(self) -> None:
        # у CLI Lab100 нет подкоманды `research` — фабрика зовёт `ask --capability deep-research`
        argv = Lab100Adapter(self.settings)._command("research", {"query": "вопрос"})
        self.assertIn("ask", argv)
        self.assertIn("--capability", argv)
        self.assertIn("deep-research", argv)
        self.assertNotIn("research", argv)

    def test_search_flags(self) -> None:
        adapter = Lab100Adapter(self.settings)
        argv = adapter._command("search", {"query": "q"})
        self.assertIn("search", argv)
        self.assertIn("--no-llm", argv)
        argv = adapter._command("search", {"query": "q", "provider": "exa"})
        self.assertIn("--search", argv)
        self.assertIn("exa", argv)

    def test_scrape_argument(self) -> None:
        argv = Lab100Adapter(self.settings)._command("scrape", {"url": "http://example.org"})
        self.assertIn("scrape", argv)
        self.assertEqual(argv[-1], "http://example.org")


if __name__ == "__main__":
    unittest.main()
