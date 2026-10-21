"""Тесты супервизора арсенала: режимы, право управления, действия."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from factory.settings import Settings
from factory.supervisor import Supervisor, _expand_command


class SupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.toml").write_text("", encoding="utf-8")
        self.settings_value = {
            "operation": {"mode": "attached"},
            "supervision": {"autostart": "auto"},
            "services": {
                "missing-svc": {
                    "title": "Пропавшая служба", "kind": "file",
                    "path": str(self.root / "missing.txt"), "owner": "factory", "enabled": True,
                },
            },
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _settings(self, **updates) -> Settings:
        data = {k: (dict(v) if isinstance(v, dict) else v)
                for k, v in self.settings_value.items()}
        for key, value in updates.items():
            data[key] = value
        return Settings(self.root / "config" / "settings.toml", data)

    def test_autostart_resolution(self) -> None:
        self.assertFalse(Supervisor(self._settings(), root=self.root).autostart)  # attached + auto
        standalone = self._settings(operation={"mode": "standalone"})
        self.assertTrue(Supervisor(standalone, root=self.root).autostart)
        forced_off = self._settings(supervision={"autostart": False})
        self.assertFalse(Supervisor(forced_off, root=self.root).autostart)
        forced_on = self._settings(supervision={"autostart": True})
        self.assertTrue(Supervisor(forced_on, root=self.root).autostart)

    def test_no_heal_flag(self) -> None:
        sup = Supervisor(self._settings(operation={"mode": "standalone"}), root=self.root)
        self.assertTrue(sup.autostart)
        sup._no_heal = True
        self.assertFalse(sup.autostart)

    def test_can_manage_rules(self) -> None:
        sup = Supervisor(self._settings(), root=self.root)
        ok, _ = sup.can_manage({"class": "service", "owner": "factory", "meta": {}})
        self.assertTrue(ok)
        ok, reason = sup.can_manage({"class": "service", "owner": "opencode", "meta": {}})
        self.assertFalse(ok)
        self.assertIn("coexistence", reason)
        ok, _ = sup.can_manage({"class": "service", "owner": "external",
                                "meta": {"docker": True}})
        self.assertFalse(ok)
        ok, reason = sup.can_manage({"class": "tool", "owner": "factory", "meta": {}})
        self.assertFalse(ok)
        self.assertIn("служб", reason)

    def test_action_refuses_forbidden_elements(self) -> None:
        sup = Supervisor(self._settings(), root=self.root)
        result = sup.action("service:docker", "up")
        self.assertFalse(result["ok"])
        result = sup.action("repo:scripts-tools", "up")
        self.assertFalse(result["ok"])
        result = sup.action("нет-такого", "up")
        self.assertFalse(result["ok"])
        self.assertIn("не найден", result["error"])

    def test_snapshot_and_tick(self) -> None:
        sup = Supervisor(self._settings(), root=self.root)
        snapshot = sup.snapshot(save=True)
        self.assertEqual(snapshot["mode"], "attached")
        ids = {node["id"] for node in snapshot["nodes"]}
        self.assertIn("service:missing-svc", ids)
        missing = next(n for n in snapshot["nodes"] if n["id"] == "service:missing-svc")
        self.assertEqual(missing["state"], "fault")
        self.assertTrue((self.root / "state" / "arsenal.json").exists())

        # в attached-режиме авто-восстановление выключено → действий нет
        result = sup.tick()
        self.assertEqual(result["mode"], "attached")
        self.assertIsInstance(result["actions"], list)

    def test_exec_helper(self) -> None:
        empty = Supervisor._exec([], timeout=5, background=False, label="x")
        self.assertFalse(empty["ok"])
        ok = Supervisor._exec([sys.executable, "--version"], timeout=30,
                              background=False, label="x")
        self.assertTrue(ok["ok"])

    def test_command_expands_environment_and_home(self) -> None:
        """Команды запуска служб раскрывают %VAR% и «~» (переносимые пути)."""
        os.environ["UNITOOL_TEST_DIR"] = r"C:\some\dir"
        expanded = _expand_command(["node", "%UNITOOL_TEST_DIR%\\server.mjs", "~\\x"])
        self.assertEqual(expanded[1], r"C:\some\dir\server.mjs")
        self.assertNotIn("~", expanded[2])
        self.assertTrue(expanded[2].endswith("x"))


if __name__ == "__main__":
    unittest.main()
