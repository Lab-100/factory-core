"""Адаптер браузерных веб-чатов — доступ к web-чатам через локальный сервис.

Оборачивает скрытый браузерный оркестратор браузерных чатов (DeepSeek,
GigaChat, Qwen, Яндекс/Алиса…). Оркестратор работает как отдельный процесс
(демон) — HTTP-сервис browser-chats (<factory root>/browser-chats/python/
webchat_service.py), а этот адаптер делает к нему синхронные HTTP-запросы.
Так асинхронная Playwright-логика не попадает в синхронное ядро фабрики.

Вопрос отправляется сразу во все включённые веб-чаты; можно ограничить
набор параметром `chats`. Ответы собираются по имени чата в `output.chats`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import Adapter, StepResult


class WebChatAdapter(Adapter):
    name = "web_chat"

    def _base(self) -> str:
        return str(self.settings.get("webchat.url", "http://127.0.0.1:8120")).rstrip("/")

    def health(self) -> dict:
        base = self._base()
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {"adapter": self.name, "available": True, "url": base, **data}
        except Exception as exc:  # noqa: BLE001 - недоступность демона не критична
            return {"adapter": self.name, "available": False, "url": base,
                    "error": str(exc)}

    def _post(self, path: str, payload: dict, timeout_s: int) -> dict:
        request = urllib.request.Request(
            self._base() + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)

    def available(self) -> bool:
        return self.health().get("available", False)

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        prompt = str(params.get("prompt") or params.get("query") or "")
        timeout_s = int(params.get("timeout_s", 90))
        chats = params.get("chats")
        if dry_run:
            return StepResult(
                True, "simulated", self.name, action,
                {"chats": chats or ["deepseek", "gigachat", "qwen", "yandex"],
                 "prompt": prompt[:200]})
        if not prompt.strip():
            return StepResult(False, "error", self.name, action,
                              error="пустой вопрос (нет prompt/query)")
        payload: dict = {"question": prompt}
        if chats:
            payload["chats"] = chats
        try:
            data = self._post("/api/ask", payload, timeout_s)
        except urllib.error.HTTPError as exc:
            return StepResult(False, "error", self.name, action,
                              error=f"HTTP {exc.code} от web-chat демона: {exc.reason}")
        except urllib.error.URLError as exc:
            return StepResult(
                False, "unavailable", self.name, action,
                error=f"web-chat демон недоступен по {self._base()}: {exc.reason} — "
                      "запустите `python webchat_service.py` в browser-chats/python")
        except Exception as exc:  # noqa: BLE001
            return StepResult(False, "error", self.name, action, error=str(exc))

        if isinstance(data, dict) and data.get("error"):
            return StepResult(False, "error", self.name, action, error=str(data["error"]))

        # Собираем ответы из JSON вида {chat: {status, answer, error}}
        answers: dict = {}
        first_answer = ""
        any_answered = False
        if isinstance(data, dict):
            for name, item in data.items():
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "")
                answer = str(item.get("answer") or "").strip()
                error = str(item.get("error") or "").strip()
                answers[name] = {
                    "status": status,
                    "answer": answer[:4000],
                    "error": error,
                }
                if status == "answered" and answer:
                    any_answered = True
                    if not first_answer:
                        first_answer = answer
        if not any_answered:
            details = "; ".join(
                f"{name}:{a.get('error') or a.get('status')}" for name, a in answers.items()
            ) or "нет ответов"
            return StepResult(False, "error", self.name, action,
                              output={"chats": answers},
                              error=f"ни один веб-чат не ответил ({details})")
        return StepResult(True, "ok", self.name, action,
                          {"chats": answers, "content": first_answer[:4000],
                           "answered": len(answers)})