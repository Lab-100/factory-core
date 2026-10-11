"""Тесты CLI: регистрация новых команд и помощников вывода."""
from __future__ import annotations

import unittest

from factory.ui.cli import _fmt_num, _inventory_detail, _ledger_detail, build_parser


class CliTest(unittest.TestCase):
    def test_new_commands_registered(self) -> None:
        parser = build_parser()
        for name in ("inventory", "quotas", "watchdog", "ledger", "manifest", "catalog"):
            args = parser.parse_args([name])
            self.assertTrue(callable(getattr(args, "func", None)),
                            f"команда {name} без обработчика")

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
