"""Адаптер локальной LLM (Ollama, /api/chat) — бесплатно и офлайн.

Поддерживает мультимодальные модели: если в параметрах переданы пути к
изображениям (`image`/`images`), они кодируются в base64 и уходят в поле
`images` сообщения — так компетенция `vision` действительно работает с
картинками, а не только с текстом (§4.5).

Устойчивость (§5): адаптер не «валит» задачу непонятной ошибкой. Если
запрошенной модели нет в Ollama, он (при `adapters.llm_local_auto_model`,
по умолчанию) один раз пробует первую доступную модель; если моделей нет
или сервер недоступен — возвращает внятную причину со списком доступных
моделей и адресом сервера.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from .base import Adapter, StepResult


class LocalLlmAdapter(Adapter):
    name = "llm_local"

    def _host(self) -> str:
        return str(self.settings.get("adapters.ollama_host", "http://127.0.0.1:11434")).rstrip("/")

    def _default_model(self) -> str:
        return str(self.settings.get("adapters.llm_local_model", "hermes3:3b") or "hermes3:3b")

    def available(self) -> bool:
        # Локальные модели (Ollama) по умолчанию ОТКЛЮЧЕНЫ — включаются
        # переключателем `adapters.llm_local_enabled = true` в settings.toml.
        return bool(self.settings.get("adapters.llm_local_enabled", False))

    def models(self) -> list[str]:
        """Имена моделей, установленных в Ollama (пусто, если сервер недоступен)."""
        try:
            with urllib.request.urlopen(self._host() + "/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - недоступность сервера не критична
            return []
        return [str(m.get("name")) for m in data.get("models", []) if m.get("name")]

    def health(self) -> dict:
        info: dict = {"adapter": self.name, "available": self.available(), "host": self._host()}
        try:
            with urllib.request.urlopen(self._host() + "/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            info["server"] = "ok"
            info["models"] = models[:20]
            info["models_count"] = len(models)
            if not models:
                info["hint"] = ("в Ollama нет установленных моделей — шаги answer/vision/code "
                                "будут падать; загрузите модель: ollama pull <имя>")
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

    def _chat(self, model: str, message: dict, timeout_s: int) -> dict:
        """Один запрос к /api/chat. Исключения не гасятся — их разбирает `run`."""
        payload = json.dumps({
            "model": model,
            "messages": [message],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self._host() + "/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        host = self._host()
        model = str(params.get("model") or self._default_model())
        timeout_s = int(params.get("timeout_s", 180))
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

        used = model
        fallback = False
        try:
            data = self._chat(used, message, timeout_s)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return StepResult(False, "error", self.name, action,
                                  error=f"HTTP {exc.code} от Ollama ({host}): {exc.reason}")
            # 404 = модели нет: один раз пробуем первую доступную (если разрешено)
            available = self.models()
            auto = bool(self.settings.get("adapters.llm_local_auto_model", True))
            if auto and available and available[0] != used:
                used, fallback = available[0], True
                try:
                    data = self._chat(used, message, timeout_s)
                except Exception as retry_exc:  # noqa: BLE001
                    return StepResult(
                        False, "error", self.name, action,
                        output={"model_requested": model, "model_used": used},
                        error=f"модель '{model}' не найдена; повтор с '{used}' не удался: {retry_exc}")
            else:
                listed = ", ".join(available) if available else "нет установленных моделей"
                return StepResult(
                    False, "error", self.name, action,
                    output={"model_requested": model, "available": available},
                    error=f"модель '{model}' не найдена в Ollama ({host}); доступны: {listed}")
        except TimeoutError:
            return StepResult(False, "error", self.name, action,
                              output={"model_requested": model},
                              error=f"таймаут {timeout_s} с ожидания модели '{model}' ({host})")
        except urllib.error.URLError as exc:
            return StepResult(False, "error", self.name, action,
                              error=f"Ollama недоступна по {host}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))

        content = data.get("message", {}).get("content", "")
        output: dict = {"model": used, "content": content[:4000]}
        if used != model:
            output["model_requested"] = model
            output["model_fallback"] = True
        if images:
            output["images"] = len(images)
        status = "ok_fallback" if fallback else "ok"
        return StepResult(True, status, self.name, action, output)
