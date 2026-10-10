"""Квоты исполнителей — этап 4 дорожной карты (пульт: домены/очередь/квоты).

Лимиты берутся из `[quota.<provider>]` репозитория Lab100
(`config/agents.toml`): `window` (day|month|forever) и `limit`. Расход ведёт
зеркальный учёт фабрики в `state/quotas.json`, чтобы оператор видел остаток
прямо в пульте. Без `limit` квота считается неограниченной.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import inventory

WINDOW_SECONDS = {"hour": 3600, "day": 86400, "month": 2592000}


def _base(settings) -> Path:
    return Path(settings.path).parent.parent


class QuotaStore:
    """Зеркальный учёт расхода квот (JSON)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    def used(self, provider: str) -> float:
        return float((self.data.get(provider) or {}).get("used", 0) or 0)

    def record(self, provider: str, amount: float = 1) -> float:
        bucket = self.data.setdefault(provider, {"used": 0})
        bucket["used"] = float(bucket.get("used", 0) or 0) + float(amount)
        self.save()
        return bucket["used"]

    def reset(self, provider: str) -> None:
        self.data.pop(provider, None)
        self.save()


def snapshot(quota_cfg: dict, store: QuotaStore) -> dict:
    out: dict[str, dict] = {}
    for provider, section in (quota_cfg or {}).items():
        section = section or {}
        limit = section.get("limit")
        used = store.used(provider)
        if limit is None:
            remaining: float | None = None
            percent: float | None = None
        else:
            remaining = max(float(limit) - used, 0.0)
            percent = round(remaining / float(limit) * 100, 1) if float(limit) else 0.0
        out[provider] = {
            "window": section.get("window", "forever"),
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "percent": percent,
            "reserve_portions": section.get("reserve_portions"),
            "unlimited": limit is None,
        }
    return out


def store_for(settings) -> QuotaStore:
    return QuotaStore(_base(settings) / str(settings.get("quotas.path", "state/quotas.json")))


def build(settings) -> dict:
    """Снимок квот: лимиты из Lab100 + расход фабрики."""
    repo, config = inventory.lab100_config(settings)
    quota_cfg = config.get("quota") or {}
    store = store_for(settings)
    return {
        "schema": "invr.quotas/1",
        "available": repo is not None,
        "source": str(repo / "config" / "agents.toml") if repo else None,
        "providers": snapshot(quota_cfg, store),
    }


def record(settings, provider: str, amount: float = 1) -> float:
    return store_for(settings).record(provider, amount)


def reset(settings, provider: str | None = None) -> None:
    store = store_for(settings)
    if provider:
        store.reset(provider)
    else:
        store.data = {}
        store.save()
