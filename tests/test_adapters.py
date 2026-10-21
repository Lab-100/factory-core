"""Тесты адаптеров: соответствие командам внешних репозиториев и запуск шимов."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path

from factory.adapters.invr_tool import InvrToolAdapter
from factory.adapters.lab100 import Lab100Adapter
from factory.adapters.llm_local import LocalLlmAdapter
from factory.adapters.unitool import UnitoolAdapter
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


class LocalLlmResilienceTest(unittest.TestCase):
    """Устойчивость локальной LLM: понятные ошибки и подмена отсутствующей модели."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        settings_path = root / "config" / "settings.toml"
        settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(settings_path, {
            "adapters": {"llm_local_model": "hermes3:3b", "llm_local_auto_model": True}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _adapter(self, fail_names, available):
        settings = self.settings

        class Flaky(LocalLlmAdapter):
            def models(self):
                return list(available)

            def _chat(self, model, message, timeout_s):
                if model in set(fail_names):
                    raise urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 404,
                                                 "model not found", {}, None)
                return {"message": {"content": "ok:" + model}}

        return Flaky(settings)

    def test_missing_model_falls_back_to_available(self) -> None:
        adapter = self._adapter({"hermes3:3b"}, ["llama3:8b"])
        res = adapter.run("chat", {"prompt": "привет"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.output["model"], "llama3:8b")
        self.assertTrue(res.output.get("model_fallback"))
        self.assertEqual(res.output.get("model_requested"), "hermes3:3b")

    def test_missing_model_without_models_reports_cleanly(self) -> None:
        adapter = self._adapter({"hermes3:3b"}, [])
        res = adapter.run("chat", {"prompt": "привет"})
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "error")
        self.assertIn("не найдена", res.error)
        self.assertIn("нет установленных моделей", res.error)

    def test_auto_model_off_keeps_error_with_list(self) -> None:
        self.settings.set("adapters.llm_local_auto_model", False)
        adapter = self._adapter({"hermes3:3b"}, ["llama3:8b"])
        res = adapter.run("chat", {"prompt": "привет"})
        self.assertFalse(res.ok)
        self.assertIn("не найдена", res.error)
        self.assertIn("llama3:8b", res.error)

    def test_timeout_reported_with_model_name(self) -> None:
        class Slow(LocalLlmAdapter):
            def _chat(self, model, message, timeout_s):
                raise TimeoutError("timed out")

        res = Slow(self.settings).run("chat", {"prompt": "привет", "timeout_s": 7})
        self.assertFalse(res.ok)
        self.assertIn("таймаут 7 с", res.error)
        self.assertIn("hermes3:3b", res.error)

    def test_unreachable_server_reported(self) -> None:
        class Down(LocalLlmAdapter):
            def _chat(self, model, message, timeout_s):
                raise urllib.error.URLError("отказано в соединении")

        res = Down(self.settings).run("chat", {"prompt": "привет"})
        self.assertFalse(res.ok)
        self.assertIn("Ollama недоступна", res.error)


class UnitoolCommandTest(unittest.TestCase):
    """Сопоставление действий фабрики на команды CLI unitool.

    unitool — единый интегральный инструмент (поиск через Lab100, свободные
    модели через MultiTool, шлюз LiteLLM). У его CLI нет подкоманд `research`
    и `chat`: оба действия идут в `ask`.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        settings_path = root / "config" / "settings.toml"
        settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(settings_path, {"adapters": {"unitool_python": "python"}})
        self.adapter = UnitoolAdapter(self.settings)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_research_and_chat_map_to_ask(self) -> None:
        for action in ("ask", "research", "chat"):
            argv = self.adapter._command(action, {"prompt": "вопрос"})
            self.assertIn("ask", argv)
            self.assertNotIn("research", argv)
            self.assertNotIn("chat", argv)
            self.assertIn("вопрос", argv)

    def test_search_flags(self) -> None:
        argv = self.adapter._command("search", {"query": "q", "limit": 3, "provider": "exa"})
        self.assertIn("search", argv)
        self.assertIn("--limit", argv)
        self.assertIn("3", argv)
        self.assertIn("--provider", argv)
        self.assertIn("exa", argv)

    def test_scrape_argument(self) -> None:
        argv = self.adapter._command("scrape", {"url": "http://example.org"})
        self.assertIn("scrape", argv)
        self.assertEqual(argv[-1], "http://example.org")

    def test_service_commands_without_arguments(self) -> None:
        for action in ("status", "models", "doctor"):
            argv = self.adapter._command(action, {})
            self.assertEqual(argv[-1], action)
            self.assertNotIn(action, argv[:-1])

    def test_command_prefix_is_python_module(self) -> None:
        """По умолчанию адаптер зовёт `python -m unitool.cli …`."""
        argv = self.adapter._command("search", {"query": "q"})
        self.assertEqual(argv[:4], ["python", "-m", "unitool.cli", "search"])

    def test_engine_and_model_flags(self) -> None:
        argv = self.adapter._command(
            "ask", {"prompt": "q", "engine": "litellm", "model": "llm-fast"})
        self.assertIn("--engine", argv)
        self.assertIn("litellm", argv)
        self.assertIn("--model", argv)
        self.assertIn("llm-fast", argv)

    def test_dry_run_reports_argv(self) -> None:
        result = self.adapter.run("search", {"query": "q"}, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "simulated")
        self.assertIn("search", result.output["argv"])

    def test_health_reports_adapter(self) -> None:
        health = self.adapter.health()
        self.assertEqual(health["adapter"], "unitool")
        self.assertIn("available", health)
        self.assertIn("python", health)


if __name__ == "__main__":
    unittest.main()
