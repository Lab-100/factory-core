"""Тесты сессий фабрики: создание, история, список, переименование, удаление."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.sessions import DEFAULT_TITLE, SessionStore


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SessionStore(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_and_get(self) -> None:
        session = self.store.create(title="Первый", project="proj")
        self.assertTrue(session["id"].startswith("ses"))
        self.assertEqual(session["title"], "Первый")
        self.assertEqual(session["project"], "proj")
        self.assertEqual(session["messages"], [])
        again = self.store.get(session["id"])
        self.assertIsNotNone(again)
        self.assertEqual(again["title"], "Первый")

    def test_create_assigns_default_title(self) -> None:
        session = self.store.create()
        self.assertEqual(session["title"], DEFAULT_TITLE)

    def test_append_user_sets_auto_title(self) -> None:
        session = self.store.create()
        self.store.append(session["id"], "user", "Найди свежие новости про Python")
        session = self.store.get(session["id"])
        self.assertEqual(session["title"], "Найди свежие новости про Python")
        self.assertEqual(len(session["messages"]), 1)
        self.assertEqual(session["messages"][0]["role"], "user")

    def test_append_assistant_keeps_title(self) -> None:
        session = self.store.create(title="Тема")
        self.store.append(session["id"], "assistant", "Готово", job_id="job-1")
        session = self.store.get(session["id"])
        self.assertEqual(session["title"], "Тема")
        self.assertEqual(session["messages"][0]["job_id"], "job-1")

    def test_append_unknown_role_becomes_system(self) -> None:
        session = self.store.create()
        self.store.append(session["id"], "robot", "текст")
        session = self.store.get(session["id"])
        self.assertEqual(session["messages"][0]["role"], "system")

    def test_append_missing_session_returns_none(self) -> None:
        self.assertIsNone(self.store.append("ses-none", "user", "нет"))

    def test_ensure_creates_then_reuses(self) -> None:
        first = self.store.ensure("ses-fixed")
        second = self.store.ensure("ses-fixed", title="Игнор")
        self.assertEqual(first["id"], "ses-fixed")
        self.assertEqual(second["id"], "ses-fixed")
        self.assertEqual(self.store.get("ses-fixed")["title"], DEFAULT_TITLE)

    def test_list_sorted_and_summary(self) -> None:
        a = self.store.create(title="A")
        b = self.store.create(title="B")
        self.store.append(a["id"], "user", "вопрос A")
        listed = self.store.list()
        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0]["id"], a["id"])  # обновлена позже
        summary = [item for item in listed if item["id"] == a["id"]][0]
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["last"], "вопрос A")
        self.assertIn(b["id"], [item["id"] for item in listed])

    def test_rename_and_delete(self) -> None:
        session = self.store.create()
        renamed = self.store.rename(session["id"], "Новое имя")
        self.assertEqual(renamed["title"], "Новое имя")
        self.assertTrue(self.store.delete(session["id"]))
        self.assertIsNone(self.store.get(session["id"]))
        self.assertFalse(self.store.delete(session["id"]))

    def test_corrupted_file_ignored(self) -> None:
        self.store.dir.mkdir(parents=True, exist_ok=True)
        (self.store.dir / "broken.json").write_text("{ не json", encoding="utf-8")
        (self.store.dir / "noid.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
        self.assertEqual(self.store.list(), [])

    def test_schema_written(self) -> None:
        session = self.store.create()
        raw = json.loads((self.store.dir / f"{session['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], "invr.session/1")


if __name__ == "__main__":
    unittest.main()
