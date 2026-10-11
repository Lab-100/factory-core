"""Тесты журналов арсенала: источники, хвост, фильтры, сводка."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory import logs
from factory.settings import Settings


class LogsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.toml").write_text("", encoding="utf-8")
        (self.root / "state").mkdir()
        (self.root / "state" / "ledger.jsonl").write_text(
            json.dumps({"kind": "step", "task": "t1", "adapter": "invr", "tool": "doc-extract"}) + "\n" +
            json.dumps({"kind": "task.done", "task": "t2", "ok": True}) + "\n",
            encoding="utf-8",
        )
        (self.root / "state" / "watchdog.jsonl").write_text(
            json.dumps({"health": {"invr": True}}) + "\n", encoding="utf-8")

        self.mcp = self.root / "mcp-watchdog"
        (self.mcp / "state").mkdir(parents=True)
        (self.mcp / "state" / "current.json").write_text("{}", encoding="utf-8")
        (self.mcp / "state" / "events.log").write_text(
            "opencode started\nollama unloaded\n", encoding="utf-8")

        self.settings = Settings(self.root / "config" / "settings.toml", {
            "sources": {"mcp_watchdog_state": str(self.mcp / "state" / "current.json")},
            "logs": {"opencode_log": str(self.root / "no-such-opencode.log")},
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sources_available(self) -> None:
        listing = {s["id"]: s for s in logs.sources(self.settings)}
        self.assertTrue(listing["factory.ledger"]["available"])
        self.assertTrue(listing["factory.watchdog"]["available"])
        self.assertTrue(listing["mcp-watchdog.events"]["available"])
        self.assertFalse(listing["opencode.log"]["available"])

    def test_tail_by_source(self) -> None:
        data = logs.tail(self.settings, source="factory.ledger", limit=10)
        self.assertTrue(data["available"])
        self.assertEqual(data["count"], 2)
        self.assertTrue(all(item["source"] == "factory.ledger" for item in data["lines"]))

    def test_tail_element_filter(self) -> None:
        data = logs.tail(self.settings, source="factory.ledger", limit=10, element="doc-extract")
        self.assertEqual(data["count"], 1)
        self.assertIn("doc-extract", data["lines"][0]["line"])

    def test_aggregate_and_unknown_source(self) -> None:
        data = logs.aggregate(self.settings, limit=50)
        sources = {item["source"] for item in data["lines"]}
        self.assertIn("factory.ledger", sources)
        self.assertIn("mcp-watchdog.events", sources)

        missing = logs.tail(self.settings, source="nope.here", limit=5)
        self.assertFalse(missing["available"])
        self.assertIn("не найден", missing["error"])

    def test_level_filter_on_json(self) -> None:
        data = logs.tail(self.settings, source="factory.ledger", limit=10, level="task.done")
        self.assertEqual(data["count"], 1)


if __name__ == "__main__":
    unittest.main()
