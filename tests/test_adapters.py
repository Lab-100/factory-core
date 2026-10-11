"""Тесты адаптеров: соответствие командам внешних репозиториев и запуск шимов."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from factory.adapters.invr_tool import InvrToolAdapter
from factory.adapters.lab100 import Lab100Adapter
from factory.adapters.llm_local import LocalLlmAdapter
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


class InvrShimExecutionTest(unittest.TestCase):
    """Живой запуск шима склада INVR (реальный подпроцесс), а не только dry-run.

    Раньше проверялся только план/`simulated`; здесь шим действительно
    исполняется, и его вывод попадает в результат шага.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tools = Path(self.tmp.name) / "tools"
        self.tools.mkdir()
        config = Path(self.tmp.name) / "config"
        config.mkdir()
        settings_path = config / "settings.toml"
        settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(settings_path, {"adapters": {"invr_root": str(self.tools)}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_shim(self, tool: str, marker: str) -> Path:
        """Переносимый шим: .cmd на Windows, исполняемый sh-скрипт с .exe иначе."""
        if os.name == "nt":
            shim = self.tools / f"{tool}.cmd"
            shim.write_text(f"@echo off\r\necho {marker} %*\r\n", encoding="ascii")
        else:
            shim = self.tools / f"{tool}.exe"
            shim.write_text(f'#!/bin/sh\necho "{marker} $*"\n', encoding="utf-8")
            shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return shim

    def test_real_shim_is_executed(self) -> None:
        self._write_shim("echoer", "MARKER-OK")
        result = InvrToolAdapter(self.settings).run(
            "run", {"tool": "echoer", "args": ["a", "b"]})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.status, "ok")
        self.assertIn("MARKER-OK", result.output["stdout"])
        self.assertIn("a", result.output["stdout"])
        self.assertIn("b", result.output["stdout"])

    def test_missing_shim_is_unavailable(self) -> None:
        result = InvrToolAdapter(self.settings).run("run", {"tool": "нет-такого"})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("нет-такого", result.error)

    def test_dry_run_does_not_require_shim(self) -> None:
        result = InvrToolAdapter(self.settings).run(
            "run", {"tool": "нет-такого", "args": ["x"]}, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.output["args"], ["x"])

    def test_health_lists_shims(self) -> None:
        self._write_shim("doc-extract", "X")
        health = InvrToolAdapter(self.settings).health()
        self.assertTrue(health["available"])
        self.assertEqual(Path(health["root"]), self.tools)
        self.assertIn("doc-extract", health["shims"])


class LocalLlmImagesTest(unittest.TestCase):
    """vision (§4.5): изображения кодируются и уходят в мультимодальное сообщение."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        settings_path = root / "config" / "settings.toml"
        settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(settings_path, {})
        self.image = root / "pic.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\ndata")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_images_encoded_from_path(self) -> None:
        images = LocalLlmAdapter(self.settings)._images({"image": str(self.image)})
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].startswith("iVBOR"))  # PNG в base64

    def test_missing_image_ignored(self) -> None:
        adapter = LocalLlmAdapter(self.settings)
        self.assertEqual(adapter._images({"image": str(Path(self.tmp.name) / "нет.png")}), [])

    def test_dry_run_reports_image_count(self) -> None:
        res = LocalLlmAdapter(self.settings).run("vision", {"image": str(self.image)},
                                                 dry_run=True)
        self.assertTrue(res.ok)
        self.assertEqual(res.output["images"], 1)


if __name__ == "__main__":
    unittest.main()
