#!/usr/bin/env python3
"""Каскадный мост фабрики external_agent -> исполнители (stdin -> stdout).

Фабрика запускает мост, передаёт промпт в stdin и ждёт ответ в stdout.
Мост пробует исполнителей ПО ПОРЯДКУ и возвращает первый непустой ответ:

  ORDER (по умолчанию):
    1. pollinations  — облачная безключевая модель (качественно)   [headless]
    2. koda          — Koda Code / Koda Desktop (CLI-движок Koda)  [headless]
    3. cline         — Cline CLI                                    [headless]
    4. multitool     — MultiTool CLI                                [headless]
    5. opencode      — бесплатные модели (не имеет окна)            [headless]
    6. workai-gui    — файл-сессия WorkAI (GUI-окно)                [окно]
    7. ollama        — локальная LLM (офлайн-фолбэк)                [headless]
    8. codex         — Codex CLI (другое приложение, не Koda)       [headless]
    9. claude-code   — Claude Code CLI (-p), в самом конце           [headless]

  Для каждого каскада ведётся журнал таймингов (запрос->ответ), записывается
  файл-ответ (JSON) и контролируется зависание. Если первый каскад не сработал
  (нет ответа/ошибка/лимит) — пробуем следующий.

Порядок переопределяется env CASСАDE_ORDER (разделитель запятая).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from ollama_sandbox import default_model, ollama_generate, log, ensure_logdir, LOGDIR

# Порядок ротации (по просьбе заказчика):
#   1. pollinations    (облачный, без окон, бесплатный);
#   2. koda            (Koda Code / Koda Desktop — консольный движок Koda);
#   3. cline           (Cline CLI);
#   4. multitool       (MultiTool CLI);
#   5. opencode        (OpenCode CLI);
#   6. workai-gui      (GUI WorkAI — всегда через защищённый мост);
#   7. ollama          (локальные модели, если включены);
#   8. codex           (Codex CLI — НЕ путать с Koda: другое приложение);
#   9. claude-code     (в самом КОНЦЕ: нестабилен — не должен блокировать
#      надёжные звенья).
# Звенья без установленной команды/окружения пропускаются автоматически
# (каскад идёт дальше); убрать любое звено можно отказавшись от него в
# CASCADE_ORDER (env) или отключив провайдера на дашборде.
DEFAULT_ORDER = "pollinations,koda,cline,multitool,opencode,workai-gui,ollama,codex,claude-code"
DEBUG = os.environ.get("CASCADE_DEBUG") == "1"
TIMEOUT_S = int(os.environ.get("CASCADE_TIMEOUT", "180"))
MAX_TOTAL_S = int(os.environ.get("CASCADE_MAX_TOTAL", "150"))
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
# Файл «потока размышлений»: фабрика передаёт путь через CASCADE_STREAM_FILE,
# мост пишет туда этапы и порции ответа — чат показывает их разворачиваемым
# блоком, не дожидаясь конца обработки.
STREAM_FILE = os.environ.get("CASCADE_STREAM_FILE", "").strip()


def _stream(line: str) -> None:
    if not STREAM_FILE:
        return
    try:
        with open(STREAM_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _bad(text: str) -> bool:
    """Признак «получен не ответ, а ошибка/лимит»."""
    if not text:
        return True
    low = text[:400].lower()
    marks = ("rate limit", "quota", "429", "insufficient", "unauthorized",
             "command not found", "is not recognized", "not available",
             "error", "panic", "timeout", "не найдена", "нет ответа",
             "unreachable", "refused", "budget", "лимит", "исчерпан",
             "exceeded", "limit", "ошибка")
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


def try_codex(prompt: str) -> tuple[bool, str, str]:
    """Codex CLI (OpenAI). Если команды нет — честный пропуск (каскад идёт
    дальше по ротации, а не падает)."""
    codex = os.environ.get("CODEX_CMD", "codex")
    if shutil.which(codex) is None and not os.path.isfile(codex):
        return False, "", "codex: команда не найдена"
    try:
        proc = subprocess.run(
            [codex, "exec", "--", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "", f"codex: таймаут {TIMEOUT_S} с"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"codex: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", f"codex: rc={proc.returncode}, пустой/ошибка"
    return True, text, ""


def try_koda(prompt: str) -> tuple[bool, str, str]:
    """Koda (Koda Code / Koda Desktop) — второе звено ротации.

    Koda Desktop использует встроенный консольный движок Koda Code, поэтому
    мост говорит с ним напрямую: `koda -p "<промпт>"` печатает ответ в stdout.
    Команда переопределяется env KODA_CMD; иначе ищем koda/koda.cmd в PATH и
    в типовых местах установки. Нет команды — честный пропуск, каскад идёт
    дальше.
    """
    koda = os.environ.get("KODA_CMD", "").strip()
    if not koda:
        for cand in ("koda", "koda.cmd"):
            found = shutil.which(cand)
            if found:
                koda = found
                break
    if not koda:
        local = os.environ.get("LOCALAPPDATA", "")
        for cand in (
            os.path.join(local, "hermes", "node", "koda.cmd") if local else "",
            os.path.join(local, "Programs", "koda-app", "Koda Desktop.exe") if local else "",
        ):
            if cand and os.path.isfile(cand):
                koda = cand
                break
    if not koda:
        return False, "", "koda: команда не найдена"
    try:
        proc = subprocess.run(
            [koda, "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "", f"koda: таймаут {TIMEOUT_S} с"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"koda: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", f"koda: rc={proc.returncode}, пустой/ошибка"
    return True, text, ""


def try_cline(prompt: str) -> tuple[bool, str, str]:
    """Cline CLI. Команда может быть переопределена CLINE_CMD; иначе ищем
    cline/cline-cli в PATH. Нет команды — пропуск, каскад идёт дальше."""
    cline = os.environ.get("CLINE_CMD", "")
    if not cline:
        for cand in ("cline", "cline-cli"):
            found = shutil.which(cand)
            if found:
                cline = found
                break
    if not cline:
        return False, "", "cline: команда не найдена"
    try:
        proc = subprocess.run(
            [cline, "--print", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "", f"cline: таймаут {TIMEOUT_S} с"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"cline: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", f"cline: rc={proc.returncode}, пустой/ошибка"
    return True, text, ""


def try_multitool(prompt: str) -> tuple[bool, str, str]:
    """MultiTool CLI. Нет команды — пропуск, каскад идёт дальше."""
    mt = os.environ.get("MULTITOOL_CMD", "")
    if not mt:
        for cand in ("multitool", "mt"):
            found = shutil.which(cand)
            if found:
                mt = found
                break
    if not mt:
        return False, "", "multitool: команда не найдена"
    try:
        proc = subprocess.run(
            [mt, "run", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "", f"multitool: таймаут {TIMEOUT_S} с"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"multitool: {exc}"
    text = (proc.stdout or "").strip()
    if _bad(text):
        return False, "", f"multitool: rc={proc.returncode}, пустой/ошибка"
    return True, text, ""


def try_workai_gui(prompt: str) -> tuple[bool, str, str]:
    # Последний вариант: GUI-окно WorkAI. Запускаем через защищённый мост
    # workai_bridge (мьютекс запуска, проверка памяти ≥20% свободно, лимит
    # инстансов, скрытие окон, автозакрытие по простою) — чтобы каскад не
    # наоткрывал кучу окон WorkAI и не забивал память.
    bridge = ""
    try:
        from .workai_bridge import _find_workai
        cmd, _exe = _find_workai()
        bridge = os.environ.get("WORKAI_BRIDGE", "") or (cmd or "")
    except Exception:  # noqa: BLE001
        bridge = os.environ.get("WORKAI_BRIDGE", "")
    if not bridge:
        return False, "", "workai-gui: WorkAI не найден"
    try:
        # полноценный мост: stdin -> stdout (read_prompt, подъём окна,
        # опрос сессии, скрытие) — все защиты уже внутри.
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "workai_bridge.py")],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return False, "", f"workai-gui: {exc}"
    if proc.returncode != 0:
        return False, "", f"workai-gui: rc={proc.returncode}, {(proc.stderr or '').strip()[:200]}"
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
    "codex": try_codex,
    "koda": try_koda,
    "cline": try_cline,
    "multitool": try_multitool,
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
    attempts: list[tuple[str, str]] = []
    log(f"=== КАСКАД старт order={order} prompt={prompt[:60]!r} max_total={MAX_TOTAL_S}s ===")
    _stream(f"[start] каскад запущен, порядок: {order}")

    for name in (n.strip() for n in order.split(",") if n.strip()):
        if time.monotonic() >= total_deadline:
            log(f"каскад: общий бюджет {MAX_TOTAL_S}с исчерпан, прекращаю", "WARN")
            attempts.append((name, f"общий бюджет {MAX_TOTAL_S}с исчерпан"))
            break
        fn = CASCADES.get(name)
        if not fn:
            log(f"каскад {name}: неизвестен, пропуск", "WARN")
            attempts.append((name, "неизвестен"))
            continue
        t = time.monotonic()
        log(f"каскад: пробую {name} ...")
        _stream(f"[try] пробую {name} …")
        try:
            ok, text, err = fn(prompt)
        except Exception as exc:  # noqa: BLE001
            ok, text, err = False, "", f"{name}: {exc!r}"
        dt_ms = int((time.monotonic() - t) * 1000)
        if ok:
            log(f"каскад: {name} ОК ({len(text)} симв., {dt_ms}ms)")
            _stream(f"[ok] {name} ответил ({len(text)} симв., {dt_ms}ms)")
            if len(text) > 400:
                for i in range(0, len(text), 400):
                    _stream(f"[chunk] {text[i:i+400]}")
            _answer_file("DONE", name, prompt, text, dt_ms, None, pid, utc_start)
            sys.stdout.write(text)
            sys.stdout.flush()
            return 0
        log(f"каскад: {name} отказ ({dt_ms}ms): {err}", "WARN")
        _stream(f"[fail] {name}: {err}")
        attempts.append((name, err or "нет ответа"))

    dur_ms = int((time.monotonic() - start) * 1000)
    msg = f"каскад: все исполнители не дали ответа ({order})"
    log(msg, "ERROR")
    _answer_file("FAILED", "", prompt, "", dur_ms, msg, pid, utc_start)
    # Отчёт заказчику: перечисляем, кого пробовали и почему не вышло,
    # и возвращаем приказ — решай сам или переотправь задачу.
    report_lines = [
        "НЕ УДАЛОСЬ ПОЛУЧИТЬ ОТВЕТ НИ ОТ ОДНОГО ИСПОЛНИТЕЛЯ.",
        f"Цепочка провайдеров ({order}):",
    ]
    for name in (n.strip() for n in order.split(",") if n.strip()):
        reason = "неизвестен/пропущен"
        for stored in reversed(attempts or []):
            if stored[0] == name:
                reason = stored[1]
                break
        report_lines.append(f"  - {name}: {reason}")
    report_lines.append(
        "ПРИКАЗ: решай сам или верни задачу заказчику (все исполнители "
        "недоступны или исчерпали лимит).")
    report = "\n".join(report_lines)
    sys.stdout.write(report)
    sys.stdout.flush()
    return 2


if __name__ == "__main__":
    sys.exit(main())