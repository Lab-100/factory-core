"""Тесты проверки апгрейда и аддитивного бэкапа."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class UpgradeTest(unittest.TestCase):
    def test_additive_backup_creates_manifest(self) -> None:
        from factory.upgrade import _additive_backup

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "file.txt"
            src.write_text("orig", encoding="utf-8")
            result = _additive_backup(root, [src])
            self.assertTrue(result["ok"])
            self.assertFalse(result.get("skipped"))
            bdir = Path(result["dir"])
            self.assertTrue(bdir.exists())
            self.assertTrue((bdir / "manifest.json").exists())
            manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), 1)

    def test_additive_backup_skips_missing(self) -> None:
        from factory.upgrade import _additive_backup

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = _additive_backup(root, [root / "нет-такого-файла"])
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("skipped"))

    def test_local_version_ok(self) -> None:
        from factory.upgrade import _local_version

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project.json").write_text(
                '{"version": "9.9.9"}', encoding="utf-8")
            self.assertEqual(_local_version(root), "9.9.9")


if __name__ == "__main__":
    unittest.main()