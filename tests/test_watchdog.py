"""Тесты супервизии фабрики — watchdog (этап 5)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.ledger import Ledger
from factory.settings import Settings
from factory.watchdog import Watchdog

DOMAINS = """
schema = "invr.factory.domains/1"
match_by = "auto"

[domain.research]
enabled = true
capabilities = ["answer"]
"""


class WatchdogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.domains_file = self.root / "config" / "domains.toml"
        self.domains_file.write_text(DOMAINS, encoding="utf-8")
        self.settings_path = self.root / "config" / "settings.toml"
        self.settings_path.write_text("", encoding="utf-8")
        self.settings = Settings(self.settings_path, {
            "adapters": {
                "lab100_dir": str(self.root / "нет"),
                "invr_root": str(self.root / "нет-инстр"),
                "region_repo": str(self.root / "нет-регион"),
            }
        })
        self.ledger = Ledger(self.root / "state" / "ledger.jsonl")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tick_records_health(self) -> None:
        dog = Watchdog(self.settings, root=self.root, ledger=self.ledger)
        result = dog.tick()
        self.assertIn("health", result)
        self.assertTrue((self.root / "state" / "watchdog.jsonl").exists())

    def test_reload_domains_on_change(self) -> None:
        dog = Watchdog(self.settings, root=self.root, ledger=self.ledger)
        dog.tick()
        # Явно сдвигаем mtime: не полагаемся на разрешение таймстемпов
        # файловой системы (на CI файл мог быть перезаписан в ту же секунду,
        # и тогда «изменения» не видно — ложное падение).
        before = self.domains_file.stat().st_mtime
        self.domains_file.write_text(DOMAINS + "\n# change\n", encoding="utf-8")
        os.utime(self.domains_file, (before + 10, before + 10))
        result = dog.tick()
        actions = [action["action"] for action in result["actions"]]
        self.assertIn("reload-domains", actions)

    def test_heal_stuck_job(self) -> None:
        state = self.root / "state"
        state.mkdir(parents=True, exist_ok=True)
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        (state / "jobs.jsonl").write_text(
            json.dumps({"id": "j1", "created": old, "updated": old,
                        "status": "running", "goal": "тест"}) + "\n",
            encoding="utf-8",
        )
        dog = Watchdog(self.settings, root=self.root)
        result = dog.tick()
        actions = [action["action"] for action in result["actions"]]
        self.assertIn("job-timeout", actions)
        # событие попало в единый журнал
        ledger = Ledger(state / "ledger.jsonl")
        self.assertTrue(any(e.get("kind") == "watchdog" for e in ledger.tail(10)))

    def test_idle_workai_closed_only_after_marker_old(self) -> None:
        """Маркер «мостового» WorkAI: пока свежий — окно не трогаем,
        после истечения порога и при отсутствии активных задач — закрываем."""
        state = self.root / "state"
        state.mkdir(parents=True, exist_ok=True)
        marker = state / "workai_bridge.txt"
        # свежий маркер — WorkAI не трогаем
        marker.write_text(str(datetime.now().timestamp()), encoding="utf-8")
        dog = Watchdog(self.settings, root=self.root)
        result = dog._collect_idle_workai()
        self.assertEqual(result, [])
        self.assertTrue(marker.exists(), "свежий маркер не удаляем")
        # старый маркер, задач нет — закрываем и снимаем маркер
        marker.write_text(str(datetime.now().timestamp() - 1000), encoding="utf-8")
        self.settings.set("watchdog.workai_idle_s", 300)
        result = dog._collect_idle_workai()
        # taskkill может вернуть ошибку (WorkAI не запущен) — но маркер снимаем
        self.assertFalse(marker.exists(), "маркер удалён после простоя")


if __name__ == "__main__":
    unittest.main()
