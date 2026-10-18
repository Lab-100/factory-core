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
        variants = providers.run_commands(self.settings, provider["name"])
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"provider": provider["name"],
                               "command": variants[0] if variants else [],
                               "commands": variants,
                               "prompt": prompt[:200]})
        if not variants:
            return StepResult(False, "unavailable", self.name, action,
                              {"provider": provider["name"]},
                              error="команда провайдера не найдена")
        prompt_arg = bool(provider.get("prompt_arg"))
        timeout = int(params.get("timeout_s", 180))
        last_error = ""
        last_code = None
        tried = 0
        for command in variants:
            tried += 1
            cmd = [*command, prompt] if prompt_arg else command
            try:
                if prompt_arg:
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=timeout, encoding="utf-8",
                                          errors="replace")
                else:
                    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                          timeout=timeout, encoding="utf-8",
                                          errors="replace")
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                continue
            if proc.returncode == 0:
                raw = proc.stdout or ""
                if provider["name"] == "cline":
                    content = providers.extract_cline_text(raw)
                else:
                    content = raw.strip()
                if content:
                    return StepResult(True, "ok", self.name, action,
                                      {"provider": provider["name"],
                                       "content": content[:4000],
                                       "tried": tried})
                last_error = "пустой ответ"
                last_code = proc.returncode
                continue
            last_error = (proc.stderr or "").strip()[:500] or f"код возврата {proc.returncode}"
            last_code = proc.returncode
        return StepResult(False, "error", self.name, action,
                          {"provider": provider["name"], "returncode": last_code,
                           "tried": tried},
                          error=last_error or "все модели не сработали")
