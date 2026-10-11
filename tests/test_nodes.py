"""Тесты узлов арсенала: проверки и состояния."""
from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from factory import nodes


def _element(**kw) -> dict:
    base = {
        "id": "x", "name": "x", "class": "service", "title": "x", "source": "services",
        "available": True, "enabled": True, "owner": "factory", "path": None,
        "probe": None, "tags": [], "meta": {},
    }
    base.update(kw)
    return base


class NodesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_exists_probe_active(self) -> None:
        target = self.root / "present.txt"
        target.write_text("x", encoding="utf-8")
        node = nodes.state_for(_element(probe={"kind": "exists", "path": str(target)}))
        self.assertEqual(node["state"], "active")

    def test_missing_factory_element_is_fault(self) -> None:
        node = nodes.state_for(_element(
            class_="tool", owner="factory",
            probe={"kind": "exists", "path": str(self.root / "nope")}))
        self.assertEqual(node["state"], "fault")

    def test_foreign_down_is_idle_attached_or_need_guard_standalone(self) -> None:
        element = _element(owner="opencode",
                           probe={"kind": "exists", "path": str(self.root / "nope")})
        self.assertEqual(nodes.state_for(element, "attached")["state"], "idle")
        self.assertEqual(nodes.state_for(element, "standalone")["state"], "need-guard")

    def test_docker_unloaded(self) -> None:
        node = nodes.state_for(_element(available=False, enabled=False, meta={"docker": True}))
        self.assertEqual(node["state"], "unloaded")
        self.assertIn("Docker", node["detail"])

    def test_disabled_unloaded(self) -> None:
        node = nodes.state_for(_element(enabled=False))
        self.assertEqual(node["state"], "unloaded")

    def test_no_probe_is_unknown(self) -> None:
        self.assertEqual(nodes.state_for(_element())["state"], "unknown")

    def test_command_probe(self) -> None:
        missing = nodes.run_probe({"kind": "command", "command": ["definitely-missing-binary-xyz"]})
        self.assertFalse(missing[0])

    def test_tcp_probe_open_and_closed(self) -> None:
        # займём свободный порт и проверим его открытость, затем закроем
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            ok, _ = nodes.run_probe({"kind": "tcp", "host": "127.0.0.1", "port": port})
            self.assertTrue(ok)
        finally:
            server.close()
        ok, _ = nodes.run_probe({"kind": "tcp", "host": "127.0.0.1", "port": port})
        self.assertFalse(ok)

    def test_snapshot_summary(self) -> None:
        target = self.root / "ok.txt"
        target.write_text("x", encoding="utf-8")
        elements = [
            _element(id="a", probe={"kind": "exists", "path": str(target)}),
            _element(id="b", owner="opencode", probe={"kind": "exists", "path": str(self.root / "no")}),
            _element(id="c", meta={"docker": True}, available=False, enabled=False),
        ]
        data = nodes.snapshot(None, {"elements": elements}, mode="attached", elements=elements)
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["working"], 1)
        self.assertEqual(data["summary"]["attention"], 0)
        self.assertEqual(data["summary"]["by_state"]["idle"], 1)
        self.assertEqual(data["summary"]["by_state"]["unloaded"], 1)


if __name__ == "__main__":
    unittest.main()
