"""Адаптер локальной LLM (Ollama, /api/chat) — бесплатно и офлайн.

Поддерживает мультимодальные модели: если в параметрах переданы пути к
изображениям (`image`/`images`), они кодируются в base64 и уходят в поле
`images` сообщения — так компетенция `vision` действительно работает с
картинками, а не только с текстом (§4.5).
"""
from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

from .base import Adapter, StepResult


class LocalLlmAdapter(Adapter):
    name = "llm_local"

    def _host(self) -> str:
        return str(self.settings.get("adapters.ollama_host", "http://127.0.0.1:11434")).rstrip("/")

    def available(self) -> bool:
        return bool(self.settings.get("adapters.llm_local_enabled", True))

    def health(self) -> dict:
        info: dict = {"adapter": self.name, "available": self.available(), "host": self._host()}
        try:
            with urllib.request.urlopen(self._host() + "/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            info["server"] = "ok"
            info["models"] = [m.get("name") for m in data.get("models", [])][:20]
        except Exception as exc:  # noqa: BLE001
            info["server"] = "unreachable"
            info["error"] = str(exc)
        return info

    @staticmethod
    def _encode(path) -> str | None:
        """Файл изображения → base64 (ASCII) или None, если прочитать не удалось."""
        try:
            return base64.b64encode(Path(path).read_bytes()).decode("ascii")
        except OSError:
            return None

    def _images(self, params: dict) -> list[str]:
        """Изображения для мультимодальной модели: base64 из файлов image/images."""
        raw = params.get("images")
        if raw is None and params.get("image"):
            raw = [params["image"]]
        if isinstance(raw, (str, Path)):
            raw = [raw]
        images: list[str] = []
        for item in raw or []:
            encoded = self._encode(item)
            if encoded:
                images.append(encoded)
        return images

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        host = self._host()
        model = str(params.get("model", "hermes3:3b"))
        prompt = str(params.get("prompt") or params.get("query") or "")
        images = self._images(params)
        if dry_run:
            info: dict = {"model": model, "prompt": prompt[:200]}
            if images:
                info["images"] = len(images)
            return StepResult(True, "simulated", self.name, action, info)
        message: dict = {"role": "user", "content": prompt}
        if images:
            # мультимодальные модели Ollama (llava, qwen2-vl, …) принимают base64
            message["images"] = images
        payload = json.dumps({
            "model": model,
            "messages": [message],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            host + "/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(params.get("timeout_s", 180))) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))
        content = data.get("message", {}).get("content", "")
        output: dict = {"model": model, "content": content[:4000]}
        if images:
            output["images"] = len(images)
        return StepResult(True, "ok", self.name, action, output)
