"""Тесты CLI: регистрация команд, помощники вывода и работа с контекстом."""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from factory.domains import DomainRegistry
from factory.ledger import Ledger
from factory.settings import Settings
from factory.ui import cli
from factory.ui.cli import _fmt_num, _inventory_detail, _ledger_detail, build_parser

DOMAINS = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.research]
enabled = true
priority = 50
capabilities = ["web-search"]
"""


class CliContextTest(unittest.TestCase):
    """Проверки команд, работающих с рабочим контекстом (домены, каталог)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config" / "profiles").mkdir(parents=True)
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")
        (self.root / "config" / "domains.toml").write_text(DOMAINS, encoding="utf-8")
        self.settings = Settings(self.settings_path, {})
        self.registry = DomainRegistry(self.root / "config" / "domains.toml", [])
        self.ledger = Ledger(self.root / "ledger.jsonl")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _patch(self):
        return mock.patch("factory.ui.cli.build_context",
                          return_value=(self.root, self.settings, self.registry, self.ledger))

    def test_domain_disable_persists(self) -> None:
        with self._patch():
            rc = cli._cmd_domains(argparse.Namespace(action="disable", name="research", value=50))
        self.assertEqual(rc, 0)
        overlay = self.root / "config" / "profiles" / "_runtime.toml"
        self.assertTrue(overlay.exists())
        reloaded = DomainRegistry(self.root / "config" / "domains.toml", [overlay])
        self.assertFalse(reloaded.get("research").enabled)

    def test_unknown_domain_is_reported(self) -> None:
        with self._patch(), redirect_stderr(io.StringIO()) as err:
            rc = cli._cmd_domains(argparse.Namespace(action="disable", name="нет", value=50))
        self.assertEqual(rc, 1)
        self.assertIn("нет", err.getvalue())

    def test_catalog_domain_filter(self) -> None:
        for domain, name in (("news", "t1"), ("weather", "t2")):
            folder = self.root / "catalog" / domain / name
            folder.mkdir(parents=True)
            (folder / "result.json").write_text(
                json.dumps({"ok": True, "task": {"goal": "цель"}, "results": [1]}),
                encoding="utf-8")
        with self._patch(), redirect_stdout(io.StringIO()) as out:
            rc = cli._cmd_catalog(argparse.Namespace(action="list", path="",
                                                    domain="news", json=False))
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("t1", text)
        # имя временного каталога может случайно содержать «t2»; проверяем домен
        self.assertNotIn("weather", text)


class CliTest(unittest.TestCase):
    def test_new_commands_registered(self) -> None:
        parser = build_parser()
        for name in ("inventory", "quotas", "watchdog", "ledger", "manifest", "catalog",
                     "heal"):
            args = parser.parse_args([name])
            self.assertTrue(callable(getattr(args, "func", None)),
                            f"команда {name} без обработчика")

    def test_heal_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["heal"])
        self.assertFalse(args.fix)
        self.assertIsNone(args.timeout)
        args = parser.parse_args(["heal", "--fix", "--commit", "--push", "--json",
                                  "--timeout", "60", "--message", "м"])
        self.assertTrue(args.fix)
        self.assertTrue(args.commit)
        self.assertTrue(args.push)
        self.assertTrue(args.json)
        self.assertEqual(args.timeout, 60.0)
        self.assertEqual(args.message, "м")

    def test_quotas_reset_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["quotas", "--reset"])
        self.assertEqual(args.reset, "")
        args = parser.parse_args(["quotas", "--reset", "exa"])
        self.assertEqual(args.reset, "exa")
        args = parser.parse_args(["quotas"])
        self.assertIsNone(args.reset)

    def test_fmt_num(self) -> None:
        self.assertEqual(_fmt_num(5.0), "5")
        self.assertEqual(_fmt_num(None), "∞")
        self.assertEqual(_fmt_num(2.5), "2.5")

    def test_ledger_detail(self) -> None:
        detail = _ledger_detail({"task": "t", "status": "ok", "adapter": "lab100"})
        self.assertIn("task=t", detail)
        self.assertIn("adapter=lab100", detail)
        self.assertIn("status=ok", detail)

    def test_inventory_detail(self) -> None:
        self.assertIn("tools=1", _inventory_detail("scripts-tools", {"count": 1, "shims": ["a"]}))
        self.assertIn("providers=2", _inventory_detail("Lab100", {"count": 2}))

    def test_catalog_args(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["catalog"])
        self.assertEqual(args.action, "list")
        self.assertEqual(args.path, "")
        args = parser.parse_args(["catalog", "show", "news/t1"])
        self.assertEqual(args.action, "show")
        self.assertEqual(args.path, "news/t1")
        args = parser.parse_args(["catalog", "--domain", "news", "--json"])
        self.assertEqual(args.domain, "news")
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
