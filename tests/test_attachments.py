"""Тесты вложений: санитайз имён, сохранение, уникализация, защита путей."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.attachments import AttachmentsStore, safe_name, safe_scope


class SafeNameTest(unittest.TestCase):
    def test_strips_directories(self) -> None:
        self.assertEqual(safe_name("C:/secret/path/report.txt"), "report.txt")
        self.assertEqual(safe_name("../../etc/passwd"), "passwd")

    def test_replaces_bad_chars(self) -> None:
        self.assertNotIn("<", safe_name("a<b>c?.txt"))
        self.assertTrue(safe_name("   ") in ("file",))

    def test_scope_sanitized(self) -> None:
        self.assertEqual(safe_scope("ses-1/good"), "ses-1_good")
        self.assertEqual(safe_scope("../../etc"), "etc")
        self.assertEqual(safe_scope(""), "default")


class AttachmentsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AttachmentsStore(self.root, max_bytes=1024)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_list(self) -> None:
        meta = self.store.save("ses1", "отчёт.txt", "привет".encode("utf-8"))
        self.assertEqual(meta["name"], "отчёт.txt")
        self.assertEqual(meta["size"], len("привет".encode()))
        self.assertTrue((self.root / meta["path"]).exists())
        items = self.store.list("ses1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["path"], meta["path"])

    def test_unique_names(self) -> None:
        first = self.store.save("ses1", "note.txt", b"1")
        second = self.store.save("ses1", "note.txt", b"2")
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(second["name"], "note-1.txt")

    def test_scope_isolation(self) -> None:
        self.store.save("a", "x.txt", b"1")
        self.assertEqual(self.store.list("b"), [])

    def test_max_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.save("ses1", "big.bin", b"x" * 2048)

    def test_resolve_blocks_traversal(self) -> None:
        self.assertIsNone(self.store.resolve("../../secret.txt"))
        meta = self.store.save("ses1", "ok.txt", b"1")
        resolved = self.store.resolve(meta["path"])
        self.assertEqual(resolved, (self.root / meta["path"]).resolve())

    def test_delete(self) -> None:
        self.store.save("ses1", "gone.txt", b"1")
        self.assertTrue(self.store.delete("gone.txt", "ses1"))
        self.assertFalse(self.store.delete("gone.txt", "ses1"))


if __name__ == "__main__":
    unittest.main()
