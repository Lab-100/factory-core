"""Тесты адаптера браузерных веб-чатов (web_chat): демон, маршрут, контракт."""
from __future__ import annotations

import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

from factory.adapters.base import Adapter, StepResult
from factory.adapters.web_chat import WebChatAdapter

# --- fixtures ---------------------------------------------------------


def _settings(root: Path, data: dict | None = None):
    (root / "config").mkdir(exist_ok=True)
    path = root / "config" / "settings.toml"
    path.write_text("", encoding="utf-8")
    from factory.settings import Settings

    return Settings(path, data or {})


def _api_response(payload: dict):
    """Фейковый HTTP-ответ для urlopen: JSON-тело и контекст возпроизводим."""
    import json

    body = json.dumps(payload).encode("utf-8")

    class FakeResp:
        status = 200
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return body

    return FakeResp()


class WebChatAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_ok(self) -> None:
        settings = _settings(self.root)
        result = WebChatAdapter(settings).run("ask", {"prompt": "привет"}, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "simulated")
        self.assertIn("deepseek", result.output["chats"])
        self.assertEqual(result.output["prompt"], "привет")

    def test_health_unavailable_when_daemon_down(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        with mock.patch.object(adapter, "_base", return_value="http://127.0.0.1:1"):
            health = adapter.health()
        self.assertFalse(health["available"])
        self.assertIn("error", health)

    def test_health_ok_when_daemon_up(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        with mock.patch.object(
            urllib.request, "urlopen", return_value=_api_response({"ok": True})
        ) as m:
            health = adapter.health()
        self.assertTrue(health["available"])
        m.assert_called_once()

    def test_run_empty_prompt(self) -> None:
        settings = _settings(self.root)
        result = WebChatAdapter(settings).run("ask", {"prompt": "   "})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")

    def test_run_unavailable_raises_urlerror(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        with mock.patch.object(adapter, "_post",
                               side_effect=urllib.error.URLError("connection refused")):
            result = adapter.run("ask", {"prompt": "привет"})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("webchat_service.py", result.error)

    def test_run_http_error(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        with mock.patch.object(
            adapter, "_post",
            side_effect=urllib.error.HTTPError("http://x", 500, "Internal", {}, None),
        ):
            result = adapter.run("ask", {"prompt": "привет"})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertIn("HTTP 500", result.error)

    def test_run_parses_answers(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        payload = {
            "deepseek": {"status": "answered", "answer": "ответ от DeepSeek", "error": ""},
            "qwen": {"status": "answered", "answer": "ответ от Qwen", "error": ""},
        }
        with mock.patch.object(adapter, "_post", return_value=payload):
            result = adapter.run("ask", {"prompt": "привет"})
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")
        out = result.output
        self.assertEqual(out["chats"]["deepseek"]["answer"], "ответ от DeepSeek")
        self.assertEqual(out["content"], "ответ от DeepSeek")
        self.assertEqual(out["answered"], 2)

    def test_run_error_when_all_failed(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        payload = {
            "deepseek": {"status": "error", "answer": "", "error": "нужен вход"},
            "qwen": {"status": "error", "answer": "", "error": "нет ответа"},
        }
        with mock.patch.object(adapter, "_post", return_value=payload):
            result = adapter.run("ask", {"prompt": "привет"})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertIn("ни один веб-чат не ответил", result.error)

    def test_run_service_error_field(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        with mock.patch.object(adapter, "_post", return_value={"error": "пустой вопрос"}):
            result = adapter.run("ask", {"prompt": "привет"})
        self.assertFalse(result.ok)
        self.assertIn("пустой вопрос", result.error)

    def test_chats_param_passed(self) -> None:
        settings = _settings(self.root)
        adapter = WebChatAdapter(settings)
        payload = {"qwen": {"status": "answered", "answer": "х", "error": ""}}
        with mock.patch.object(adapter, "_post", return_value=payload) as m:
            adapter.run("ask", {"prompt": "вопрос", "chats": ["qwen"]})
        payload_arg = m.call_args.args[1]
        self.assertEqual(payload_arg["chats"], ["qwen"])


if __name__ == "__main__":
    unittest.main()