"""Тесты: интерфейс фабрики запускается без лишних окон консоли."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = (ROOT / "scripts" / "factory.iss").read_text(encoding="utf-8-sig")
MONITOR = (ROOT / "scripts" / "micromonitor.ps1").read_text(encoding="utf-8")


class ShortcutTest(unittest.TestCase):
    def test_shortcuts_launch_hidden(self) -> None:
        icons = [line for line in ISS.splitlines() if line.startswith("Name: \"{group}")]
        gui = [line for line in icons if "ui --mode" in line or "--mode" in line]
        self.assertTrue(gui, "не найдены ярлыки интерфейса")
        for line in gui:
            self.assertIn("WindowStyle Hidden", line, f"ярлык запускается с окном: {line[:80]}")

    def test_desktop_icon_launches_hidden(self) -> None:
        line = next(line for line in ISS.splitlines() if line.startswith("Name: \"{autodesktop}"))
        self.assertIn("WindowStyle Hidden", line)

    def test_autostart_launches_hidden(self) -> None:
        line = next(line for line in ISS.splitlines() if "ValueName: \"FactoryLab100\"" in line)
        self.assertIn("WindowStyle Hidden", line)

    def test_status_shortcut_stays_console(self) -> None:
        # «Состояние фабрики» — это вывод в консоль, его скрывать нельзя
        line = next(line for line in ISS.splitlines()
                    if line.startswith("Name: \"{group}\\Состояние фабрики\""))
        self.assertNotIn("WindowStyle Hidden", line)


class MonitorTest(unittest.TestCase):
    def test_target_started_hidden(self) -> None:
        self.assertIn("-WindowStyle Hidden", MONITOR)

    def test_output_is_redirected_to_files(self) -> None:
        self.assertIn("-RedirectStandardOutput", MONITOR)
        self.assertIn("-RedirectStandardError", MONITOR)

    def test_single_instance_guard_present(self) -> None:
        self.assertIn("Микромонитор запуска — Фабрика Lab-100", MONITOR)


if __name__ == "__main__":
    unittest.main()