"""Тесты микромонитора запуска: перехват вывода, ошибок и журналы."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from factory import monitor
from factory.ui.cli import build_parser


class DetectionTest(unittest.TestCase):
    def test_traceback_is_error(self) -> None:
        self.assertTrue(monitor.looks_like_error("Traceback (most recent call last):"))

    def test_russian_error_marker(self) -> None:
        self.assertTrue(monitor.looks_like_error("Ошибка: файл не найден"))

    def test_unavailable_marker(self) -> None:
        self.assertTrue(monitor.looks_like_error("Оконный режим недоступен: установите pywebview"))

    def test_clean_line_is_not_error(self) -> None:
        self.assertFalse(monitor.looks_like_error("готово, 0 errors"))
        self.assertFalse(monitor.looks_like_error(""))
        self.assertFalse(monitor.looks_like_error("всё в порядке"))

    def test_source_extracted_from_path(self) -> None:
        text = "сбой в C:\\project\\factory\\core.py: строка 12"
        self.assertIn("core.py", monitor.extract_source(text, "stdout"))

    def test_source_falls_back_to_stream(self) -> None:
        self.assertEqual(monitor.extract_source("просто текст", "stderr"), "stderr")

    def test_default_target_is_window_mode(self) -> None:
        target = monitor.default_target()
        self.assertIn("ui", target)
        self.assertIn("desktop", target)


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _monitor(self, code: str) -> monitor.LaunchMonitor:
        return monitor.LaunchMonitor(
            [sys.executable, "-c", code], root=self.root,
            log_path=self.root / "state" / "m.log",
            errors_path=self.root / "state" / "m-errors.log",
        )

    def test_clean_run_is_ok(self) -> None:
        report = self._monitor("print('привет')").run(timeout=60)
        self.assertTrue(report["ok"])
        self.assertEqual(report["returncode"], 0)
        self.assertEqual(report["errors"], [])

    def test_stderr_and_exit_code_are_errors(self) -> None:
        code = "import sys; sys.stderr.write('Ошибка: бум\\n'); sys.exit(3)"
        report = self._monitor(code).run(timeout=60)
        self.assertFalse(report["ok"])
        self.assertEqual(report["returncode"], 3)
        self.assertTrue(report["errors"])
        self.assertIn("бум", report["report"])
        self.assertIn("кодом 3", report["report"])

    def test_error_marker_on_stdout_is_error(self) -> None:
        report = self._monitor("print('Ошибка: не удалось открыть файл')").run(timeout=60)
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])

    def test_entry_format_is_column(self) -> None:
        entry = {"at": "14.10.2026 18:05:01", "stream": "stderr",
                 "source": "core.py", "text": "бум", "error": True}
        line = monitor.LaunchMonitor._format_entry(entry)
        self.assertEqual(line, "[14.10.2026 18:05:01] core.py: бум")

    def test_logs_are_written(self) -> None:
        report = self._monitor("import sys; sys.stderr.write('сбой\\n')").run(timeout=60)
        self.assertTrue(Path(report["log"]).exists())
        self.assertTrue(Path(report["errors_log"]).exists())
        self.assertIn("сбой", Path(report["errors_log"]).read_text(encoding="utf-8"))

    def test_missing_command_is_reported(self) -> None:
        monitor_obj = monitor.LaunchMonitor(
            ["этой-команды-точно-нет-xyz"], root=self.root)
        report = monitor_obj.run(timeout=30)
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])

    def test_clean_run_report_is_empty(self) -> None:
        report = self._monitor("print('x')").run(timeout=60)
        self.assertEqual(report["report"], "")


class CliTest(unittest.TestCase):
    def test_parser_has_monitor(self) -> None:
        parser = build_parser("ru")
        args = parser.parse_args(["monitor", "--json"])
        self.assertTrue(args.json)
        self.assertTrue(callable(args.func))

    def test_target_after_double_dash(self) -> None:
        parser = build_parser("ru")
        args = parser.parse_args(["monitor", "--", "status"])
        self.assertEqual(args.target, ["--", "status"])


if __name__ == "__main__":
    unittest.main()
