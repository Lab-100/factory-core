"""Адаптер доступа/смены региона (region-spoof).

Приоритет: внешний CLI region-spoof (если есть) → правка config.json → недоступно.
Протокол описан в docs/REGION_PROTOCOL.md.
"""
from __future__ import annotations

from .. import regionlib
from .base import Adapter, StepResult


class RegionAdapter(Adapter):
    name = "region"

    def _repo(self):
        return regionlib.find_repo(self.settings)

    def available(self) -> bool:
        return bool(self.settings.get("adapters.region_enabled", True)) and self._repo() is not None

    def health(self) -> dict:
        repo = self._repo()
        info: dict = {"adapter": self.name, "available": repo is not None,
                      "repo": str(repo) if repo else None}
        if repo is not None:
            try:
                info.update(regionlib.status(repo))
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)
            info["cli"] = regionlib.find_cli(repo)
        return info

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        repo = self._repo()
        region = str(params.get("region", "US"))
        zone = params.get("zone")
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"region": region, "zone": zone, "repo": str(repo) if repo else None})
        if repo is None:
            return StepResult(False, "unavailable", self.name, action,
                              error="репозиторий region-spoof не найден")
        cli = regionlib.find_cli(repo)
        try:
            if cli:
                data = regionlib.call_cli(cli, action, region=region, zone=zone)
                ok = bool(data.get("ok"))
                return StepResult(ok, "ok" if ok else "error", self.name, action, data,
                                  None if ok else data.get("error"))
            data = regionlib.switch(repo, region=region, zone=zone)
            data["note"] = ("config.json обновлён; приложение применит регион при следующем "
                            "запуске (для горячего переключения — см. docs/REGION_PROTOCOL.md)")
            return StepResult(True, "ok", self.name, action, data)
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
