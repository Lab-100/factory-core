"""Тесты карты инфраструктуры (§4.7): чтение состояния инструмента infra-graph.

Проверяется честная деградация: нет файла состояния — нет выдуманных узлов;
битый JSON — не исключение; список узлов вместо словаря — тоже принимается.
Внешних вызовов нет: снимок графа (``-Shot``) здесь не запускается.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import infragraph


NODES = {
    "schema": "invr.nodes/1",
    "savedAt": "2026-10-15T12:29:59+03:00",
    "nodes": {
        "monitor": {
            "Val": 1.0, "State": "active", "Owner": "system", "Intent": "auto",
            "Class": "core", "Wanted": True, "IdleSec": 3, "Pid": 111, "Err": 0,
        },
        "browsertool": {
            "Val": 0.0, "State": "unloaded", "Owner": "opencode", "Intent": "none",
            "Class": "demand", "Wanted": False, "IdleSec": 6303, "Pid": "",
        },
        "ollama": {
            "Val": 0.5, "State": "idle", "Owner": "system", "Intent": "auto",
            "Class": "service", "Wanted": True, "IdleSec": 100, "Pid": "42",
        },
    },
    "candidates": {"browsertool": {"Class": "demand", "IdleSec": 6303}},
}


class InfraGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "nodes.json"
        env = mock.patch.dict("os.environ",
                              {"INFRAGRAPH_STATE_FILE": str(self.state),
                               "INVR_TOOLS_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def test_missing_state_is_honest(self) -> None:
        data = infragraph.load()
        self.assertFalse(data["available"])
        self.assertEqual(data["nodes"], [])
        self.assertEqual(data["total"], 0)
        self.assertIn(str(self.state), data["path"])

    def test_broken_json_does_not_raise(self) -> None:
        self.state.write_text("{ это не json", encoding="utf-8")
        data = infragraph.load()
        self.assertFalse(data["available"])
        self.assertTrue(data["state_exists"])

    def test_nodes_are_sorted_by_class_and_counted(self) -> None:
        self.state.write_text(json.dumps(NODES, ensure_ascii=False), encoding="utf-8")
        data = infragraph.load()
        self.assertTrue(data["available"])
        self.assertEqual([n["id"] for n in data["nodes"]], ["monitor", "ollama", "browsertool"])
        self.assertEqual(data["counts"], {"active": 1, "idle": 1, "unloaded": 1})
        self.assertEqual(data["classes"], {"core": 1, "service": 1, "demand": 1})
        monitor = data["nodes"][0]
        self.assertEqual(monitor["points_percent"], 100)
        self.assertEqual(monitor["class"], "core")
        self.assertEqual(monitor["owner"], "system")
        self.assertEqual(monitor["pid"], "111")
        self.assertEqual(data["candidates"][0]["id"], "browsertool")
        self.assertEqual(data["saved_at"], NODES["savedAt"])

    def test_nodes_as_list_are_accepted(self) -> None:
        payload = {"nodes": [{"id": "npm", "Val": 0.0, "State": "idle", "Class": "service"}]}
        self.state.write_text(json.dumps(payload), encoding="utf-8")
        data = infragraph.load()
        self.assertEqual([n["id"] for n in data["nodes"]], ["npm"])

    def test_events_are_read_from_neighbour_log(self) -> None:
        self.state.write_text(json.dumps(NODES), encoding="utf-8")
        (self.root / "events.log").write_text("строка один\n\nстрока два\n", encoding="utf-8")
        data = infragraph.load(limit=1)
        self.assertEqual(data["events"], ["строка два"])

    def test_snapshot_without_tool_reports_error(self) -> None:
        result = infragraph.snapshot()
        self.assertFalse(result["ok"])
        self.assertIn("infra-graph", result["error"])

    def test_snapshot_requires_ps1_shim(self) -> None:
        """Одной ``.cmd``-обёртки мало: аргументы она не передаёт — честная ошибка."""
        (self.root / "infra-graph.cmd").write_text("@echo off\n", encoding="utf-8")
        result = infragraph.snapshot()
        self.assertFalse(result["ok"])
        self.assertIn("infra-graph.ps1", result["error"])

    def test_snapshot_runs_ps1_with_shot_flag(self) -> None:
        """Снимок запускает ``.ps1`` и передаёт ему ``-File`` и ``-Shot <png>``."""
        (self.root / "infra-graph.ps1").write_text("# заглушка\n", encoding="utf-8")
        captured: dict = {}

        class _Proc:
            returncode = 0
            stdout = "готово"
            stderr = ""

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            Path(argv[argv.index("-Shot") + 1]).write_bytes(b"PNG")
            return _Proc()

        with mock.patch("factory.infragraph.subprocess.run", fake_run):
            result = infragraph.snapshot()
        self.assertTrue(result["ok"], result.get("error"))
        files = [a for a in captured["argv"] if a.endswith("infra-graph.ps1")]
        self.assertEqual(len(files), 1, captured["argv"])
        self.assertIn("-Shot", captured["argv"])


if __name__ == "__main__":
    unittest.main()
