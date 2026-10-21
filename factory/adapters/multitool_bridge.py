#!/usr/bin/env python3
"""Мост Фабрики -> MultiTool (stdin -> stdout).

Фабрика запускает исполнителя и подаёт промпт в stdin, ожидая ответ в stdout.
MultiTool здесь — локальная OpenAI-совместимая служба (по умолчанию
http://127.0.0.1:8799), а не CLI, поэтому мост делает HTTP-запрос.

Переменные окружения:
    MULTITOOL_URL    базовый адрес службы (по умолчанию http://127.0.0.1:8799)
    MULTITOOL_MODEL  модель (по умолчанию unorouter/gpt-4o:free)
    MULTITOOL_TIMEOUT секунды ожидания (по умолчанию 90)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def read_prompt() -> str:
    data = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not data.strip():
        data = " ".join(sys.argv[1:])
    return data.strip()


def main() -> int:
    prompt = read_prompt()
    if not prompt:
        print("multitool: пустой промпт", file=sys.stderr)
        return 2

    base = os.environ.get("MULTITOOL_URL", "http://127.0.0.1:8799").rstrip("/")
    model = os.environ.get("MULTITOOL_MODEL", "unorouter/gpt-4o:free")
    timeout = float(os.environ.get("MULTITOOL_TIMEOUT", "90"))

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("MULTITOOL_MAX_TOKENS", "1200")),
    }
    request = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        print(f"multitool: HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - мост обязан вернуть понятную причину
        print(f"multitool: служба недоступна: {exc}", file=sys.stderr)
        return 1

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print(f"multitool: неожиданный ответ: {str(body)[:300]}", file=sys.stderr)
        return 1

    if text is None or not str(text).strip():
        print("multitool: пустой ответ модели", file=sys.stderr)
        return 1
    print(str(text).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
