"""Тесты путей фабрики: автообнаружение склада инструментов INVR-Tools."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import paths


class ToolsRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _isolate_env(self) -> None:
        """Убрать переменные склада, чтобы проверить именно автообнаружение."""
        for name in ("INVR_TOOLS_ROOT", "DEVSTATION_TOOLS"):
            os.environ.pop(name, None)

    def test_from_env(self) -> None:
        keep = self.root / "custom-store"
        other = self.root / "other-store"
        for store in (keep, other):
            store.mkdir()
        with mock.patch.dict(os.environ, {"INVR_TOOLS_ROOT": str(keep),
                                          "DEVSTATION_TOOLS": str(other)}):
            self.assertEqual(paths.tools_root(), keep)

    def test_autodiscover_home_devstation(self) -> None:
        home = self.root / "home"
        store = home / ".devstation" / "tools"
        (store / "registry" / "doc-extract" / "1.0.0").mkdir(parents=True)
        (store / "resolve-tools.ps1").write_text("#", encoding="utf-8")
        with mock.patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            self._isolate_env()
            self.assertEqual(paths.tools_root(), store)

    def test_ignores_plain_directory(self) -> None:
        # каталог без признаков склада (нет resolve-tools и registry) не принимается
        home = self.root / "empty-home"
        plain = home / ".devstation" / "tools"
        plain.mkdir(parents=True)
        with mock.patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            self._isolate_env()
            self.assertNotEqual(paths.tools_root(), plain)


if __name__ == "__main__":
    unittest.main()
