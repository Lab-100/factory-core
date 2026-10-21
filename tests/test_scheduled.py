"""Тесты модуля авто-надзора (scheduled) — статус/переключатель задачи."""
from __future__ import annotations

import unittest
from unittest import mock


class ScheduledTest(unittest.TestCase):
    def test_status_structure(self) -> None:
        from factory import scheduled

        st = scheduled.status()
        self.assertIn("task", st)
        self.assertIn("enabled", st)
        self.assertIn("running", st)
        self.assertIn("exists", st)

    def test_toggle_requires_windows(self) -> None:
        from factory import scheduled

        with mock.patch.object(scheduled, "_IS_WIN", False):
            st = scheduled.status()
            self.assertFalse(st.get("available"))
            res = scheduled.set_enabled(True)
            self.assertFalse(res.get("ok"))


if __name__ == "__main__":
    unittest.main()