#!/usr/bin/env python3
"""Мост фабрики external_agent -> Ollama-песочница (stdin -> stdout).

Фабрика (external_agent) запускает команду провайдера и передаёт промпт в
stdin, ожидая ответ в stdout. Этот мост — замена для `workai_bridge.py`:
вместо хрупкого GUI-окна WorkAI он использует headless-запрос к локальной
Ollama (без окон, без эмуляции ввода мыши/клавиатуры), печатает ответ в
stdout и завершается.

Подключение в фабрике (providers.<имя>.bridge):
    python ollama_bridge.py
Ответ — только текст модели в stdout; диагностика — в stderr.
"""
from __future__ import annotations

import os
import sys

from ollama_sandbox import default_model, ollama_generate, log, ensure_logdir


def read_prompt() -> str:
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data and data.strip():
                return data.strip()
    except Exception:  # noqa: BLE001
        pass
    return " ".join(a for a in sys.argv[1:] if not a.startswith("-")).strip()


def main() -> int:
    prompt = read_prompt()
    if not prompt:
        print("ERROR: пустой промпт (stdin пуст)", file=sys.stderr)
        return 1
    timeout_s = int(os.environ.get("OLLAMA_SANDBOX_TIMEOUT", "180"))
    model = os.environ.get("OLLAMA_SANDBOX_MODEL", "") or default_model()
    debug = os.environ.get("OLLAMA_SANDBOX_DEBUG") == "1"
    if debug:
        log(f"bridge: model={model} timeout={timeout_s}s prompt={prompt[:80]!r}")
    try:
        data = ollama_generate(model, prompt, timeout_s)
    except TimeoutError:
        print(f"ERROR: таймаут {timeout_s} с — Ollama не ответила", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    text = (data.get("text") or "").strip()
    if not text:
        print("ERROR: пустой ответ от модели", file=sys.stderr)
        return 4
    sys.stdout.write(text)
    sys.stdout.flush()
    if debug:
        log(f"bridge: ответ {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main())