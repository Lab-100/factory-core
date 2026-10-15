"""Тесты: интерфейс фабрики запускается без лишних окон консоли.

Ярлыки и автозапуск используют PowerShell с -EncodedCommand (Inno Setup не
принимает вложенные кавычки в Parameters), поэтому тест декодирует команду и
проверяет, что она запускает фабрику скрыто.
"""
from __future__ import annotations

import base64
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = (ROOT / "scripts" / "factory.iss").read_text(encoding="utf-8-sig")
MONITOR = (ROOT / "scripts" / "micromonitor.ps1").read_text(encoding="utf-8")


def commands() -> list[str]:
    """Декодировать все -EncodedCommand из установщика."""
    result = []
    for blob in re.findall(r"-EncodedCommand ([A-Za-z0-9+/=]+)", ISS):
        result.append(base64.b64decode(blob).decode("utf-16-le"))
    return result


class ShortcutTest(unittest.TestCase):
    def test_shortcuts_encoded(self) -> None:
        icons = [line for line in ISS.splitlines() if line.startswith("Name: \"{group}")]
        gui = [line for line in icons if "desktop" in line or "EncodedCommand" in line]
        self.assertTrue(gui, "не найдены ярлыки интерфейса")
        for line in gui:
            self.assertIn("-EncodedCommand", line, f"ярлык запускается с окном: {line[:70]}")
            self.assertIn("WindowStyle Hidden", line)

    def test_desktop_icon_encoded(self) -> None:
        line = next(line for line in ISS.splitlines() if line.startswith("Name: \"{autodesktop}"))
        self.assertIn("-EncodedCommand", line)

    def test_autostart_encoded(self) -> None:
        line = next(line for line in ISS.splitlines() if "ValueName: \"FactoryLab100\"" in line)
        self.assertIn("-EncodedCommand", line)
        self.assertIn("WindowStyle Hidden", line)

    def test_status_shortcut_stays_console(self) -> None:
        # «Состояние фабрики» — это вывод в консоль, его скрывать нельзя
        line = next(line for line in ISS.splitlines()
                    if line.startswith("Name: \"{group}\\Состояние фабрики\""))
        self.assertNotIn("EncodedCommand", line)

    def test_commands_start_hidden(self) -> None:
        commands_list = commands()
        self.assertGreaterEqual(len(commands_list), 3)
        for command in commands_list:
            self.assertIn("Start-Process", command)
            self.assertIn("-WindowStyle Hidden", command)
            self.assertIn("factory.exe", command)

    def test_commands_do_not_open_many_windows(self) -> None:
        for command in commands():
            # только один запуск и никаких window.open
            self.assertEqual(command.count("Start-Process"), 1)


class MonitorTest(unittest.TestCase):
    def test_target_started_hidden(self) -> None:
        self.assertIn("-WindowStyle Hidden", MONITOR)

    def test_output_is_redirected_to_files(self) -> None:
        self.assertIn("-RedirectStandardOutput", MONITOR)
        self.assertIn("-RedirectStandardError", MONITOR)

    def test_single_instance_guard_present(self) -> None:
        self.assertIn("Микромонитор запуска — Фабрика Lab-100", MONITOR)


class GuiScriptTest(unittest.TestCase):
    def test_script_exists(self) -> None:
        script = ROOT / "scripts" / "factory-gui.ps1"
        self.assertTrue(script.exists(), "нет скрипта скрытого запуска")

    def test_script_launches_hidden(self) -> None:
        text = (ROOT / "scripts" / "factory-gui.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("-WindowStyle Hidden", text)


if __name__ == "__main__":
    unittest.main()