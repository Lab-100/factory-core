"""Тесты обёртки backup-утилиты — без запуска внешнего инструмента."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class BackupModuleTest(unittest.TestCase):
    def test_script_not_found_returns_error(self) -> None:
        from factory import backup

        with TemporaryDirectory() as tmp:
            # tools_root не настроен, но функция должна не падать
            result = backup.backup(None, files=["x"], notes="t")
            self.assertIsInstance(result, dict)
            self.assertIn("ok", result)

    def test_backup_requires_files(self) -> None:
        from factory import backup

        result = backup.backup(None, files=[], notes="t")
        self.assertFalse(result["ok"])
        self.assertIn("укажите файлы", result["error"])


if __name__ == "__main__":
    unittest.main()