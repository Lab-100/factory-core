"""Адаптер доступа/смены региона (region-spoof).

Управление region-spoof (GUI/трей) пока не имеет CLI-протокола, поэтому адаптер
готовит переключение и фиксирует необходимость ручного/будущего шага.
"""
from __future__ import annotations

from pathlib import Path

from .base import Adapter, StepResult


class RegionAdapter(Adapter):
    name = "region"

    def available(self) -> bool:
        return bool(self.settings.get("adapters.region_enabled", True))

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        region = str(params.get("region", "US"))
        if dry_run:
            return StepResult(True, "simulated", self.name, action, {"region": region})
        repo = self.settings.get("adapters.region_repo") or ""
        found = Path(repo).exists() if repo else False
        return StepResult(True, "noop", self.name, action, {
            "region": region,
            "region_repo": str(repo),
            "found": found,
            "note": "region-spoof управляется через свой трей; автопереключение — TODO (нужен CLI-протокол)",
        })
