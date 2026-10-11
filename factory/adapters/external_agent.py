"""Адаптер внешнего ИИ-агента — встроенные ассистенты сред разработки.

Запускает CLI включённого провайдера (OpenCode, Claude Code, Cursor, Windsurf,
Void, Zen, workAI, MultiTool) и возвращает его ответ. Провайдер берётся из
параметра шага `provider` или из первого включённого; в dry-run команда не
запускается. Провайдеры включаются переключателем (`factory providers enable
<имя>` или раздел «Провайдеры» в дашборде).
"""
from __future__ import annotations

import subprocess

from .. import providers
from .base import Adapter, StepResult


class ExternalAgentAdapter(Adapter):
    name = "external_agent"

    def available(self) -> bool:
        return bool(providers.enabled(self.settings))

    def health(self) -> dict:
        items = providers.discover(self.settings)
        return {
            "adapter": self.name,
            "available": self.available(),
            "enabled": [item["name"] for item in items if item["enabled"]],
            "found": [item["name"] for item in items if item["available"]],
        }

    def _pick(self, params: dict) -> dict | None:
        wanted = str(params.get("provider") or "").strip()
        candidates = providers.enabled(self.settings)
        if wanted:
            return next((item for item in candidates if item["name"] == wanted), None)
        return candidates[0] if candidates else None

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        provider = self._pick(params)
        prompt = str(params.get("prompt") or params.get("query") or "")
        if provider is None:
            return StepResult(False, "unavailable", self.name, action,
                              error="нет включённых внешних провайдеров")
        command = providers.run_command(self.settings, provider["name"])
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"provider": provider["name"], "command": command,
                               "prompt": prompt[:200]})
        if not command:
            return StepResult(False, "unavailable", self.name, action,
                              {"provider": provider["name"]},
                              error="команда провайдера не найдена")
        try:
            proc = subprocess.run(command, input=prompt, capture_output=True,
                                  text=True, timeout=int(params.get("timeout_s", 180)),
                                  encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action,
                              {"provider": provider["name"]}, error=str(exc))
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()[:500]
            return StepResult(False, "error", self.name, action,
                              {"provider": provider["name"], "returncode": proc.returncode},
                              error=detail or f"код возврата {proc.returncode}")
        return StepResult(True, "ok", self.name, action,
                          {"provider": provider["name"],
                           "content": (proc.stdout or "").strip()[:4000]})
