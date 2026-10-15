"""Тесты охраны «только один экземпляр» интерфейса фабрики."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import single
from factory.ui.cli import build_parser


class NameTest(unittest.TestCase):
    def test_name_contains_scope_and_user(self) -> None:
        name = single.mutex_name("ui", user="tester")
        self.assertIn("FactoryLab100", name)
        self.assertIn("ui", name)
        self.assertIn("tester", name)

    def test_name_is_session_scoped(self) -> None:
        # Local\ — объекты в сессии пользователя: не нужны права глобального объекта
        self.assertTrue(single.mutex_name("ui", user="tester").startswith("Local\\"))


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # уникальная область на каждый тест — тесты не мешают друг другу
        self.scope = f"test-{os.getpid()}-{id(self)}"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _guard(self, **kwargs) -> single.InstanceGuard:
        return single.InstanceGuard(root=self.root, scope=self.scope, **kwargs)

    def test_first_acquire_is_free(self) -> None:
        guard = self._guard()
        self.assertFalse(guard.acquire())
        self.assertTrue(guard.held)
        guard.release()

    def _helper_process(self) -> subprocess.Popen:
        """Отдельный процесс, который держит мьютекс и ждёт своей очереди."""
        script = self.root / "holder.py"
        script.write_text(
            "import time\n"
            "from factory import single\n"
            f"guard = single.InstanceGuard(root={str(self.root)!r}, scope={self.scope!r})\n"
            "assert not guard.acquire(), 'мьютекс должен быть свободен'\n"
            "print('READY', flush=True)\n"
            "time.sleep(60)\n",
            encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        proc = subprocess.Popen(
            [sys.executable, str(script)], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=env)
        line = proc.stdout.readline()
        self.assertIn("READY", line, f"держатель не стартовал: {line!r}")
        return proc

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        proc.kill()
        proc.wait(timeout=15)
        try:
            proc.stdout.close()
        except OSError:
            pass

    def test_second_process_is_blocked(self) -> None:
        proc = self._helper_process()
        try:
            guard = self._guard()
            self.assertTrue(guard.acquire(), "второй процесс должен быть заблокирован")
        finally:
            self._stop(proc)

    def test_lock_is_released_when_process_dies(self) -> None:
        # Мьютекс — объект ядра: после смерти держателя блокировка снимается сама,
        # поэтому фабрика не может «залипнуть» навсегда.
        proc = self._helper_process()
        self._stop(proc)
        guard = self._guard()
        self.assertFalse(guard.acquire(), "после падения держателя мьютекс свободен")
        guard.release()

    def test_second_guard_in_same_process_is_reentrant(self) -> None:
        # Мьютекс Windows реентерабельный: тот же поток входит повторно.
        # Разные экземпляры приложения — это разные процессы (см. тесты выше).
        first = self._guard()
        second = self._guard()
        self.assertFalse(first.acquire())
        self.assertFalse(second.acquire())
        second.release()
        first.release()

    def test_release_allows_next_instance(self) -> None:
        first = self._guard()
        first.acquire()
        first.release()
        second = self._guard()
        self.assertFalse(second.acquire())     # освободилось
        second.release()

    def test_allow_multiple_never_blocks(self) -> None:
        first = self._guard()
        second = self._guard(allow_multiple=True)
        self.assertFalse(first.acquire())
        self.assertFalse(second.acquire())
        second.release()
        first.release()

    def test_owner_metadata_is_recorded(self) -> None:
        guard = self._guard(mode="web")
        guard.acquire()
        data = single._read_owner(guard.path)
        self.assertEqual(data.get("pid"), os.getpid())
        self.assertEqual(data.get("mode"), "web")
        self.assertIn("started", data)
        guard.release()

    def test_owner_is_cleared_on_release(self) -> None:
        guard = self._guard()
        guard.acquire()
        guard.release()
        self.assertFalse(guard.path.exists())

    def test_describe_owner_reports_pid(self) -> None:
        guard = self._guard(mode="desktop")
        guard.acquire()
        info = single.describe_owner(guard)
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["mode"], "desktop")
        guard.release()

    def test_stale_owner_is_ignored(self) -> None:
        guard = self._guard()
        single._write_owner(guard.path, {
            "pid": 999999999, "scope": self.scope, "mode": "web",
            "started": "01.01.2026 00:00:00",
        })
        self.assertEqual(guard.owner(), {})   # владелец мёртв — запись протухла

    def test_release_without_acquire_is_safe(self) -> None:
        self._guard().release()

    def test_owner_file_removed_atomically(self) -> None:
        guard = self._guard()
        guard.acquire()
        single.record_instance("http://127.0.0.1:1/", self.root, self.scope)
        leftovers = list(self.root.glob("state/*.tmp"))
        self.assertEqual(leftovers, [], "временные файлы не должны оставаться")
        guard.release()

    def test_foreign_owner_file_survives_release(self) -> None:
        guard = self._guard()
        single._write_owner(guard.path, {
            "pid": os.getpid() + 1, "scope": self.scope, "mode": "web",
        })
        guard._held = True          # считаем, что захватили
        guard.release()             # файл чужого владельца трогать нельзя
        self.assertTrue(guard.path.exists())


class RecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scope = f"test-{os.getpid()}-{id(self)}"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_url_by_owner(self) -> None:
        guard = single.InstanceGuard(root=self.root, scope=self.scope, mode="web")
        guard.acquire()
        single.record_instance("http://127.0.0.1:8787/", self.root, self.scope)
        self.assertEqual(guard.owner().get("url"), "http://127.0.0.1:8787/")
        guard.release()

    def test_record_url_ignored_for_foreign_owner(self) -> None:
        guard = single.InstanceGuard(root=self.root, scope=self.scope)
        single._write_owner(guard.path, {"pid": 999999999, "scope": self.scope})
        single.record_instance("http://127.0.0.1:8787/", self.root, self.scope)
        self.assertIsNone(single._read_owner(guard.path).get("url"))

    def test_open_url_rejects_empty(self) -> None:
        self.assertFalse(single.open_url(""))


class WindowTest(unittest.TestCase):
    def test_titles_cover_all_languages(self) -> None:
        for title in ("Фабрика Lab-100", "Factory Lab-100", "工厂 Lab-100"):
            self.assertIn(title, single.WINDOW_TITLES)

    @unittest.skipUnless(sys.platform == "win32", "только для Windows")
    def test_focus_returns_none_or_pid(self) -> None:
        result = single.focus_window(("Фабрика точно-нет-12345",))
        self.assertIsNone(result)

    def test_is_alive_for_current_process(self) -> None:
        self.assertTrue(single.is_alive(os.getpid()))

    def test_is_alive_for_impossible_pid(self) -> None:
        self.assertFalse(single.is_alive(0))


class CliTest(unittest.TestCase):
    def test_ui_has_allow_multiple_flag(self) -> None:
        parser = build_parser("ru")
        args = parser.parse_args(["ui", "--mode", "web", "--allow-multiple"])
        self.assertTrue(args.allow_multiple)

    def test_ui_default_is_single_instance(self) -> None:
        parser = build_parser("ru")
        args = parser.parse_args(["ui", "--mode", "desktop"])
        self.assertFalse(args.allow_multiple)


class SettingsTest(unittest.TestCase):
    def test_settings_example_allows_single_instance(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "config" / "settings.toml"
                ).read_text(encoding="utf-8")
        self.assertIn("allow_multiple = false", text)


if __name__ == "__main__":
    unittest.main()