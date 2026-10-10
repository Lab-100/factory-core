"""Единый журнал фабрики (ledger.jsonl).

Сюда пишутся задачи, шаги, результаты, а также (при интеграции) квоты
из Lab100 token_tracker и ChangeId из backup-util.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: dict) -> dict:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        out: list[dict] = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
