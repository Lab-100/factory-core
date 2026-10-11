"""Тесты экспорта заказов и сессий (markdown / JSON / ZIP)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from factory import export
from factory.sessions import SessionStore


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state").mkdir()
        self.job = {
            "id": "job-abc",
            "created": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-01T00:01:00+00:00",
            "status": "done",
            "goal": "найди данные",
            "domain": "research",
            "automation": "auto",
            "dry_run": False,
            "result": {"ok": True, "catalog": "catalog/research/job-abc",
                       "results": [{"status": "ok", "capability": "web-search"}]},
            "error": None,
        }
        (self.root / "state" / "jobs.jsonl").write_text(
            json.dumps(self.job, ensure_ascii=False) + "\n", encoding="utf-8")
        # каталог артефактов для проверки ZIP
        cat = self.root / "catalog" / "research" / "job-abc"
        cat.mkdir(parents=True)
        (cat / "report.md").write_text("# отчёт\n", encoding="utf-8")
        (cat / "result.json").write_text("{}", encoding="utf-8")
        # сессия
        self.session = SessionStore(self.root).create(title="Проба")
        SessionStore(self.root).append(self.session["id"], "user", "вопрос")
        SessionStore(self.root).append(self.session["id"], "assistant", "ответ")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_job_markdown(self) -> None:
        data, mime, name = export.export_job(self.root, "job-abc", "md")
        text = data.decode("utf-8")
        self.assertEqual(name, "job-job-abc.md")
        self.assertIn("text/markdown", mime)
        self.assertIn("найди данные", text)
        self.assertIn("web-search", text)

    def test_job_json(self) -> None:
        data, mime, name = export.export_job(self.root, "job-abc", "json")
        self.assertEqual(mime, "application/json")
        self.assertEqual(json.loads(data)["id"], "job-abc")

    def test_job_zip_contains_catalog(self) -> None:
        data, mime, name = export.export_job(self.root, "job-abc", "zip")
        self.assertEqual(mime, "application/zip")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("job-job-abc.json", names)
        self.assertTrue(any(n.endswith("report.md") for n in names))

    def test_job_missing_is_none(self) -> None:
        self.assertIsNone(export.export_job(self.root, "нет-такого", "md"))

    def test_session_exports(self) -> None:
        data, mime, name = export.export_session(self.root, self.session["id"], "md")
        text = data.decode("utf-8")
        self.assertIn("вопрос", text)
        self.assertIn("ответ", text)
        data, mime, _name = export.export_session(self.root, self.session["id"], "json")
        self.assertEqual(json.loads(data)["id"], self.session["id"])

    def test_export_dispatch(self) -> None:
        self.assertIsNotNone(export.export(self.root, "session", self.session["id"], "json"))
        self.assertIsNotNone(export.export(self.root, "job", "job-abc", "json"))
        self.assertIsNone(export.export(self.root, "job", "нет", "json"))


if __name__ == "__main__":
    unittest.main()
