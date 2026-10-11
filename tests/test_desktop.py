"""Тесты оконного интерфейса: разбор порта и корректный откат без pywebview."""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from factory.ui import desktop


class DesktopTest(unittest.TestCase):
    def test_free_port(self) -> None:
        port = desktop._free_port()
        self.assertGreater(port, 0)

    def test_serve_without_pywebview_returns_1(self) -> None:
        settings = mock.Mock()
        settings.get.side_effect = lambda key, default=None: {
            "ui.web.host": "127.0.0.1", "ui.web.port": 8787,
        }.get(key, default)
        with mock.patch.object(desktop.web, "build_context",
                               return_value=(None, settings, None, None)), \
             mock.patch.object(desktop.web, "make_translator",
                               return_value=lambda key, **kwargs: key), \
             mock.patch("builtins.print"), \
             mock.patch.dict(sys.modules, {"webview": None}):
            code = desktop.serve()
        self.assertEqual(code, 1)

    def test_cli_accepts_desktop_mode(self) -> None:
        from factory.ui.cli import build_parser
        parser = build_parser("ru")
        args = parser.parse_args(["ui", "--mode", "desktop"])
        self.assertEqual(args.mode, "desktop")


if __name__ == "__main__":
    unittest.main()
