"""Адаптер локальной LLM (Ollama, /api/chat) — бесплатно и офлайн."""
from __future__ import annotations

import json
import urllib.request

from .base import Adapter, StepResult


class LocalLlmAdapter(Adapter):
    name = "llm_local"

    def available(self) -> bool:
        return bool(self.settings.get("adapters.llm_local_enabled", True))

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        host = str(self.settings.get("adapters.ollama_host", "http://127.0.0.1:11434")).rstrip("/")
        model = str(params.get("model", "hermes3:3b"))
        prompt = str(params.get("prompt") or params.get("query") or "")
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"model": model, "prompt": prompt[:200]})
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            host + "/api/chat", data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(params.get("timeout_s", 180))) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
        content = data.get("message", {}).get("content", "")
        return StepResult(True, "ok", self.name, action, {"model": model, "content": content[:4000]})
