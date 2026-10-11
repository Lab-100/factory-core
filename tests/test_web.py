"""Тесты веб-дашборда: реальные HTTP-запросы ко всем эндпоинтам (§5.5).

Поднимает штатный `http.server` на свободном порту с изолированным контекстом
(свой корень, домены, журнал) и проверяет выдачу `/` и `/api/*`, включая запуск
задачи, статус очереди и подтверждение шага полуавтомата. Внешних вызовов нет —
задачи запускаются в dry-run.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from factory.domains import DomainRegistry
from factory.ledger import Ledger
from factory.settings import Settings
from factory.ui import web

DOMAINS = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.research]
enabled = true
priority = 50
capabilities = ["web-search", "answer"]
"""

TERMINAL = {"done", "failed", "error", "stopped"}


class WebDashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        domains_file = self.root / "config" / "domains.toml"
        domains_file.write_text(DOMAINS, encoding="utf-8")
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text(
            'active_profile = "default"\n\n[automation]\nmode = "auto"\n',
            encoding="utf-8",
        )
        self.settings = Settings(self.settings_path, {
            "active_profile": "default",
            "automation": {"mode": "auto", "semi": {"checkpoints": []}},
        })
        self.registry = DomainRegistry(self.root / "config" / "domains.toml", [])
        self.ledger = Ledger(self.root / "state" / "ledger.jsonl")
        context = (self.root, self.settings, self.registry, self.ledger)
        self._patches = [
            mock.patch("factory.ui.web.build_context", return_value=context),
            mock.patch("factory.ui.web.repo_root", return_value=self.root),
            # фоновый поток очереди зовёт build_context из factory.jobs
            mock.patch("factory.jobs.build_context", return_value=context),
        ]
        for patch in self._patches:
            patch.start()
        web._MANAGER = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web._handler_class())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # без прокси: тестируем локальный сокет напрямую
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        web._MANAGER = None
        for patch in self._patches:
            patch.stop()
        self.tmp.cleanup()

    # --- утилиты ---
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str):
        try:
            with self.opener.open(self._url(path), timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def _get_text(self, path: str) -> tuple[int, str]:
        try:
            with self.opener.open(self._url(path), timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def _post(self, path: str, payload, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url(path), data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _wait_status(self, job_id: str, statuses: set[str], timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, data = self._get(f"/api/jobs/get?id={job_id}")
            if code == 200 and data.get("status") in statuses:
                return data
            time.sleep(0.02)
        raise AssertionError(f"задача {job_id} не достигла {statuses}")

    # --- страница и настройки ---
    def test_index_page_renders(self) -> None:
        with self.opener.open(self._url("/"), timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
            html = resp.read().decode("utf-8")
        self.assertIn("<html", html)
        self.assertNotIn("{{", html)  # все подстановки i18n закрыты

    def test_api_settings_get_and_set(self) -> None:
        code, data = self._get("/api/settings")
        self.assertEqual(code, 200)
        self.assertEqual(data["active_profile"], "default")
        self.assertEqual(data["automation"], "auto")

        code, data = self._get("/api/settings/set?key=automation.mode&value=semi")
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["value"], "semi")
        self.assertTrue(self.settings_path.read_text(encoding="utf-8"))  # настройка сохранена

        _code, data = self._get("/api/settings")
        self.assertEqual(data["automation"], "semi")

    # --- домены ---
    def test_api_domains_and_toggle(self) -> None:
        code, data = self._get("/api/domains")
        self.assertEqual(code, 200)
        names = [d["name"] for d in data["domains"]]
        self.assertIn("research", names)

        code, data = self._get("/api/domains/toggle?name=research&enabled=false")
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertTrue((self.root / "config" / "profiles" / "_runtime.toml").exists())

        _code, data = self._get("/api/domains")
        research = next(d for d in data["domains"] if d["name"] == "research")
        self.assertFalse(research["enabled"])

    def test_api_domains_toggle_unknown_is_404(self) -> None:
        code, data = self._get("/api/domains/toggle?name=ghost&enabled=true")
        self.assertEqual(code, 404)
        self.assertFalse(data["ok"])
        self.assertIn("ghost", data["error"])

    # --- мониторинг и диагностика ---
    def test_api_monitoring_and_diagnostics(self) -> None:
        code, data = self._get("/api/ledger")
        self.assertEqual(code, 200)
        self.assertIsInstance(data["events"], list)

        code, data = self._get("/api/inventory")
        self.assertEqual(code, 200)
        self.assertEqual(data["schema"], "invr.inventory/1")

        code, data = self._get("/api/quotas")
        self.assertEqual(code, 200)
        self.assertIn("available", data)

        code, data = self._get("/api/catalog")
        self.assertEqual(code, 200)
        self.assertEqual(data["schema"], "invr.catalog/1")
        self.assertIsInstance(data["items"], list)

        code, data = self._get("/api/doctor")
        self.assertEqual(code, 200)
        self.assertIsInstance(data["adapters"], list)
        self.assertIsInstance(data["integrations"], list)

    def test_api_jobs_empty_and_unknown_404(self) -> None:
        code, data = self._get("/api/jobs")
        self.assertEqual(code, 200)
        self.assertEqual(data["jobs"], [])

        code, data = self._get("/api/jobs/get?id=nope")
        self.assertEqual(code, 404)
        self.assertEqual(data["error"], "not found")

        code, data = self._get("/api/missing")
        self.assertEqual(code, 404)

    # --- запуск задач и подтверждение ---
    def test_run_auto_job_and_poll(self) -> None:
        code, data = self._post("/api/run", {
            "goal": "найди данные", "capabilities": ["web-search"], "dry_run": True,
        })
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        job_id = data["job"]["id"]

        done = self._wait_status(job_id, TERMINAL)
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["result"]["ok"])

        _code, listing = self._get("/api/jobs")
        self.assertTrue(any(j["id"] == job_id for j in listing["jobs"]))

    def test_semi_job_via_http_needs_approval(self) -> None:
        code, data = self._post("/api/run", {
            "goal": "найди", "capabilities": ["web-search"],
            "dry_run": True, "automation": "semi",
        })
        self.assertEqual(code, 200)
        job_id = data["job"]["id"]

        waiting = self._wait_status(job_id, TERMINAL | {"awaiting-approval"})
        self.assertEqual(waiting["status"], "awaiting-approval")
        self.assertTrue(waiting["awaiting"])

        code, data = self._post("/api/jobs/approve", {"id": job_id, "ok": True})
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])

        done = self._wait_status(job_id, TERMINAL)
        self.assertEqual(done["status"], "done")

    def test_approve_unknown_job_404(self) -> None:
        code, data = self._post("/api/jobs/approve", {"id": "нет-такого", "ok": True})
        self.assertEqual(code, 404)
        self.assertFalse(data["ok"])

    def test_run_bad_json_400(self) -> None:
        code, data = self._post("/api/run", None, raw=b"{not-json")
        self.assertEqual(code, 400)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "bad json")


if __name__ == "__main__":
    unittest.main()
