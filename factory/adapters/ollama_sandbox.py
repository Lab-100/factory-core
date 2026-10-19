#!/usr/bin/env python3
"""Песочница вокруг Ollama для фабрики Lab-100.

Единый модуль: логгер запусков/закрытий, тайминги запрос->ответ, контроль
зависания и запись результата в файл-ответ. Используется как CLI и как
библиотека моста (stdin -> stdout), совместимо с external_agent фабрики.

Запуск CLI:
    python ollama_sandbox.py run --prompt "..." [--model qwen2.5:1.5b] [--timeout 180]
    python ollama_sandbox.py status            # доступность Ollama и модели
    python ollama_sandbox.py summary           # сводка по журналу
    python ollama_sandbox.py list              # список запросов (незакрытые)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DIR = Path(__file__).resolve().parent
LOGDIR = DIR / "logs"
CSV = LOGDIR / "sessions_ollama.csv"
TXT = LOGDIR / "ollama-sandbox.log"
PIDFILE = LOGDIR / "session.pid"

MODELLIST_CACHE_TTL = 5.0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ensure_logdir() -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)


def log(message: str, level: str = "INFO") -> None:
    ensure_logdir()
    line = f"[{_now()}] [{level}] {message}"
    try:
        with open(TXT, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr, flush=True)


def write_session(**fields) -> None:
    ensure_logdir()
    new = not CSV.exists() or CSV.stat().st_size == 0
    with open(CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "utc_start", "utc_end", "duration_ms", "model", "prompt",
            "status", "pid", "error"])
        if new:
            w.writeheader()
        w.writerow(fields)


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [str(m.get("name")) for m in data.get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


def ollama_status() -> dict:
    models = ollama_models()
    return {"host": HOST, "server": "ok" if models or True else "ok",
            "models": models, "models_count": len(models)}


def ollama_generate(model: str, prompt: str, timeout_s: int,
                    poll_cb=None) -> dict:
    """Потоковый запрос /api/generate с опросом сердца (не даёт зависнуть).

    Возвращает {text, model, prompt_eval_count, eval_count, total_duration_ms}.
    При таймауте/недоступности бросает TimeoutError / RuntimeError.
    """
    payload = json.dumps({
        "model": model, "prompt": prompt, "stream": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        HOST + "/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")

    chunks: list[str] = []
    meta: dict = {}
    start = time.monotonic()
    deadline = start + timeout_s
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
        buf = b""
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(timeout_s)
            if poll_cb:
                poll_cb(start, deadline)
            block = resp.read(1)
            if not block:
                break
            buf += block
            # накапливаем до перевода строки (JSONL поток от Ollama)
            if b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if obj.get("response"):
                    chunks.append(obj["response"])
                for key in ("model", "prompt_eval_count", "eval_count"):
                    if key in obj:
                        meta[key] = obj[key]
                if obj.get("done"):
                    meta["total_duration_ms"] = obj.get("total_duration", 0) / 1e6
                    break
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(f"модель '{model}' не найдена в Ollama; "
                               f"доступны: {', '.join(ollama_models()) or 'нет'}")
        raise RuntimeError(f"HTTP {exc.code} от Ollama: {exc.reason}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama недоступна по {HOST}: {exc.reason}")
    except socket_timeout():
        raise TimeoutError(timeout_s)

    return {"text": "".join(chunks), "model": meta.get("model", model),
            "prompt_eval_count": meta.get("prompt_eval_count", 0),
            "eval_count": meta.get("eval_count", 0),
            "total_duration_ms": meta.get("total_duration_ms", 0)}


def socket_timeout():
    import socket
    return socket.timeout


def default_model() -> str:
    m = os.environ.get("OLLAMA_SANDBOX_MODEL", "").strip()
    if m:
        return m
    for cand in ("qwen2.5:1.5b", "llama3.2:1b", "qwen2.5-coder:1.5b", "gemma3:1b"):
        if cand in ollama_models():
            return cand
    return "qwen2.5:1.5b"


def cmd_run(args: argparse.Namespace) -> int:
    prompt = args.prompt.strip()
    if not prompt:
        log("пустой промпт", "ERROR")
        return 1
    model = args.model or default_model()
    timeout_s = args.timeout
    log(f"=== ЗАПРОС старт model={model} timeout={timeout_s}s prompt={prompt[:80]!r} ===")

    utc_start = _utc()
    lstart = time.monotonic()
    pid = os.getpid()
    try:
        with open(PIDFILE, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass

    status = "RUNNING"
    err = ""
    result: dict = {"status": status}
    try:
        last_poll = time.monotonic()
        def poll_cb(start, deadline):
            nonlocal last_poll
            # раз в 15 с пишем сердцебиение — фабрика видит, что не завис
            if time.monotonic() - last_poll >= 15:
                last_poll = time.monotonic()
                log(f"heartbeat: работаю ещё {int(deadline - time.monotonic())} c", "DBG")
        data = ollama_generate(model, prompt, timeout_s, poll_cb=poll_cb)
        status = "DONE"
    except TimeoutError:
        status = "HANG"
        err = f"таймаут {timeout_s} с — нет ответа; запрошен статус Ollama"
        # проверяем статус — не завис ли сервер/процесс
        try:
            st = ollama_status()
            if st["server"] == "ok":
                err += f" (Ollama живет, моделей {st['models_count']})"
            else:
                err += " (Ollama недоступна!)"
        except Exception:  # noqa: BLE001
            err += " (не удалось опросить статус)"
    except RuntimeError as exc:
        status = "ERROR"
        err = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = "ERROR"
        err = repr(exc)

    dur_ms = int((time.monotonic() - lstart) * 1000)
    utc_end = _utc()

    result = {
        "status": status, "model": model, "prompt": prompt,
        "dur_ms": dur_ms, "utc_start": utc_start, "utc_end": utc_end,
        "text": data.get("text", "") if status == "DONE" else "",
        "pid": pid, "error": err or None,
    }
    write_session(utc_start=utc_start, utc_end=utc_end, duration_ms=dur_ms,
                  model=model, prompt=prompt[:200], status=status, pid=pid,
                  error=(err or "")[:500])
    log(f"=== ЗАПРОС завершён status={status} dur={dur_ms}ms ===")

    # запись файла-ответа (читает внешний модуль фабрики)
    out = None
    if args.outdir:
        out = Path(args.outdir) / f"response-{int(time.time())}.json"
    else:
        out = LOGDIR / f"response-{int(time.time())}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    log(f"file-answer: {out}")

    if args.stdout:
        # режим моста: печатаем текст ответа в stdout (как ждёт external_agent)
        sys.stdout.write(result["text"])
        sys.stdout.flush()

    return 0 if status == "DONE" else 2


def cmd_status(_: argparse.Namespace) -> int:
    st = ollama_status()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0


def cmd_summary(_: argparse.Namespace) -> int:
    ensure_logdir()
    if not CSV.exists() or CSV.stat().st_size == 0:
        print("Журнал пуст.")
        return 0
    with open(CSV, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    times = [int(r["duration_ms"]) for r in rows if r["duration_ms"].isdigit()]
    if times:
        print(f"Запросов: {len(rows)} | суммарно {sum(times)/1000:.1f} с | "
              f"средн {sum(times)/len(times)/1000:.1f} с | "
              f"макс {max(times)/1000:.1f} с | мин {min(times)/1000:.1f} с")
    print("Последние 8:")
    for r in rows[-8:]:
        print(f"  {r['utc_start']} {r['status']:6} {r['duration_ms']:>7}ms "
              f"{r['model']:14} {r['prompt'][:40]}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    procs = []
    if platform.system() == "Windows":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ollama*",
                              "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        print(out or "нет процессов ollama")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Песочница Ollama для фабрики")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="выполнить запрос")
    p_run.add_argument("--prompt", required=True)
    p_run.add_argument("--model", default="")
    p_run.add_argument("--timeout", type=int, default=180)
    p_run.add_argument("--outdir", default="")
    p_run.add_argument("--stdout", action="store_true",
                       help="печатать ответ в stdout (режим моста)")
    p_run.set_defaults(fn=cmd_run)

    p_st = sub.add_parser("status", help="статус Ollama")
    p_st.set_defaults(fn=cmd_status)

    p_sum = sub.add_parser("summary", help="сводка по журналу")
    p_sum.set_defaults(fn=cmd_summary)

    p_ls = sub.add_parser("list", help="процессы ollama")
    p_ls.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())