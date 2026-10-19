#!/usr/bin/env python3
"""Каскадный мост фабрики external_agent -> исполнители (stdin -> stdout).

Фабрика запускает мост, передаёт промпт в stdin и ждёт ответ в stdout.
Мост пробует исполнителей ПО ПОРЯДКУ и возвращает первый непустой ответ:

  ORDER:
    1. pollinations  — облачная безключевая модель (качественно)   [headless]
    2. ollama        — локальная LLM (офлайн-фолбэк)               [headless]
    3. opencode      — бесплатные модели (не имеет окна)           [headless]
    4. claude-code   — Claude Code CLI (-p), если установлен        [headless]
    5. workai-gui    — файл-сессия WorkAI (GUI-окно, ПОСЛЕДНИЙ)    [окно]

  Для каждого каскада ведётся журнал таймингов (запрос->ответ), записывается
  файл-ответ (JSON) и контролируется зависание. Если первый каскад не сработал
  (нет ответа/ошибка/лимит) — пробуем следующий.

Порядок переопределяется env CASСАDE_ORDER (разделитель запятая).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from ollama_sandbox import default_model, ollama_generate, log, ensure_logdir, LOGDIR

DEFAULT_ORDER = "pollinations,ollama,opencode,claude-code,workai-gui"
DEBUG = os.environ.get("CASCADE_DEBUG") == "1"
TIMEOUT_S = int(os.environ.get("CASCADE_TIMEOUT", "180"))
MAX_TOTAL_S = int(os.environ.get("CASCADE_MAX_TOTAL", "150"))
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _bad(text: str) -> bool:
    """Признак «получен не ответ, а ошибка/лимит»."""
    if not text:
        return True
    low = text[:400].lower()
    marks = ("rate limit", "quota", "429", "insufficient", "unauthorized",
             "command not found", "is not recognized", "not available",
             "error", "panic", "timeout", "не найдена", "нет ответа",
             "unreachable", "refused")
    return any(m in low for m in marks)


def _answer_file(status: str, model: str, prompt: str, text: str,
                 dur_ms: int, err: str, pid: int, utc_start: str) -> Path:
    ensure_logdir()
    out = LOGDIR / f"cascade-answer-{int(time.time())}.json"
    out.write_text(json.dumps({
        "status": status, "model": model, "prompt": prompt,
        "dur_ms": dur_ms, "utc_start": utc_start,
        "utc_end": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "text": text, "pid": pid, "error": err or None,
        "cascade_order": os.environ.get("CASCADE_ORDER", DEFAULT_ORDER),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------- каскады ----------

def try_ollama(prompt: str) -> tuple[bool, str, str]:
    model = default_model()
    try:
        data = ollama_generate(model, prompt, TIMEOUT_S)
    except TimeoutError:
        return False, "", f"ollama: таймаут {TIMEOUT_S} с"
    except RuntimeError as exc:
        return False, "", f"ollama: {exc}"
    text = (data.get("text") or "").strip()
    if _bad(text):
        return False, "", "ollama: пустой/ошибочный ответ"
    return True, text, ""


def try_opencode(prompt: str) -> tuple[bool, str, str]:
    model = os.environ.get("CASCADE_OPENCODE_MODEL", "opencode/space-bunny-free")
    try:
        proc = subprocess.run(
            ["opencode", "run", "--model", model],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return False, "", "opencode: команда не найдена"
    except subprocess.TimeoutExpired:
        return False, "", f"opencode: таймаут {TIMEOUT_S} с"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"opencode: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", f"opencode: rc={proc.returncode}, пустой/ошибка"
    return True, text, ""


def try_pollinations(prompt: str) -> tuple[bool, str, str]:
    body = json.dumps({"model": "openai",
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(
        "https://text.pollinations.ai/openai", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    # Жёсткий короткий таймаут: pollinations бывает недоступен/медленный,
    # и нельзя чтобы фабрика ждала его полный TIMEOUT_S (висла бы).
    poll_timeout = int(os.environ.get("CASCADE_POLLINATIONS_TIMEOUT", "30"))
    try:
        with urllib.request.urlopen(req, timeout=poll_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except urllib.error.HTTPError as exc:
        return False, "", f"pollinations: HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"pollinations: {exc}"
    if _bad(text):
        return False, "", "pollinations: пустой/ошибка"
    return True, text, ""


def try_claude_code(prompt: str) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return False, "", "claude-code: команда не найдена"
    except subprocess.TimeoutExpired:
        return False, "", f"claude-code: таймаут {TIMEOUT_S} с"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"claude-code: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", f"claude-code: rc={proc.returncode}, пустой/ошибка"
    return True, text, ""


def try_workai_gui(prompt: str) -> tuple[bool, str, str]:
    # Последний вариант: GUI-окно WorkAI (как старый workai_bridge).
    bridge = os.environ.get("WORKAI_BRIDGE",
                            r"C:\Program Files\WorkAI\bin\workai.cmd")
    try:
        proc = subprocess.run(
            [bridge, "chat", prompt, "-m", "ask", "-r"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return False, "", f"workai-gui: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", "workai-gui: пустой/ошибка"
    return True, text, ""


CASCADES = {
    "ollama": try_ollama,
    "opencode": try_opencode,
    "pollinations": try_pollinations,
    "claude-code": try_claude_code,
    "workai-gui": try_workai_gui,
}


def main() -> int:
    try:
        prompt = sys.stdin.read()
    except Exception:  # noqa: BLE001
        prompt = ""
    prompt = prompt.strip()
    if not prompt:
        sys.stderr.write("ERROR: пустой промпт (stdin пуст)\n")
        return 1

    order = os.environ.get("CASCADE_ORDER", DEFAULT_ORDER)
    utc_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    start = time.monotonic()
    pid = os.getpid()
    total_deadline = start + MAX_TOTAL_S
    log(f"=== КАСКАД старт order={order} prompt={prompt[:60]!r} max_total={MAX_TOTAL_S}s ===")

    for name in (n.strip() for n in order.split(",") if n.strip()):
        if time.monotonic() >= total_deadline:
            log(f"каскад: общий бюджет {MAX_TOTAL_S}с исчерпан, прекращаю", "WARN")
            break
        fn = CASCADES.get(name)
        if not fn:
            log(f"каскад {name}: неизвестен, пропуск", "WARN")
            continue
        t = time.monotonic()
        log(f"каскад: пробую {name} ...")
        try:
            ok, text, err = fn(prompt)
        except Exception as exc:  # noqa: BLE001
            ok, text, err = False, "", f"{name}: {exc!r}"
        dt_ms = int((time.monotonic() - t) * 1000)
        if ok:
            log(f"каскад: {name} ОК ({len(text)} симв., {dt_ms}ms)")
            _answer_file("DONE", name, prompt, text, dt_ms, None, pid, utc_start)
            sys.stdout.write(text)
            sys.stdout.flush()
            return 0
        log(f"каскад: {name} отказ ({dt_ms}ms): {err}", "WARN")

    dur_ms = int((time.monotonic() - start) * 1000)
    msg = f"каскад: все исполнители не дали ответа ({order})"
    log(msg, "ERROR")
    _answer_file("FAILED", "", prompt, "", dur_ms, msg, pid, utc_start)
    sys.stderr.write(f"ERROR: {msg}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())