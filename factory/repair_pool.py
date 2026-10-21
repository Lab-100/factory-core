"""Ремонтный пул фабрики — «починка» задач после отказа штатного каскада.

Когда все звенья каскада не дали ответа, фабрика не оставляет задачу «мёртвой»:
она собирает ошибку вместе с хвостами логов (jobs, cascade-answer, сессия) и
отправляет в ремонтный пул. Пул перебирает наиболее «вдумчивые» бесплатные
модели/исполнителей (OpenCode free, Cline free, локальные и, при наличии,
Docker/MultiTool/KadaDesktop), пока кто-то не сформулирует ответ и требование
к действию. Результат возвращается как обычный ответ задачи.

Это мягкая деградация: ремонт не должен крушить основную логику, все ошибки
пула изолированы.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Кандидаты ремонтного пула — по приоритету «вдумчивости за 0 рублей».
# Формат: (имя, функция-попытка). Сначала модели с reasoning,
# затем локальные/агентные фолбэки.
def _try_opencode_free(prompt: str, timeout: int) -> tuple[bool, str, str]:
    for model in (
        "opencode/glm-5.3-flash-free",
        "opencode/nemotron-3.5-super-vl-free",
        "opencode/deepseek-v4.1-flash-free",
        "opencode/space-bunny-free",
        "opencode/mimo-v2.6-flash-free",
        "opencode/longcat-2.5-preview-free",
        "opencode/beam-free",
    ):
        cmd = ["opencode", "run", "--model", model]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            return False, "", "opencode: команда не найдена"
        except subprocess.TimeoutExpired:
            continue
        except Exception as exc:  # noqa: BLE001
            continue
        text = (proc.stdout or "").strip()
        bad = text.lower()[:400].find("rate limit") >= 0 or \
              text.lower()[:400].find("not available") >= 0 or \
              text.lower()[:400].find("error") >= 0 or not text
        if not bad:
            return True, text, model
    return False, "", "opencode: все free-модели не ответили"


def _try_cline_free(prompt: str, timeout: int) -> tuple[bool, str, str]:
    import shutil

    cline = shutil.which("cline") or shutil.which("cline-cli")
    if not cline:
        return False, "", "cline: команда не найдена"
    for model in (
        "nvidia/nemotron-3.5-super-vl:free",
        "poolside/laguna-s-2.1:free",
        "cohere/north-mini-code:free",
    ):
        try:
            proc = subprocess.run(
                [cline, "--print", "-m", model, prompt],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001
            continue
        text = (proc.stdout or "").strip()
        if text and text.lower()[:400].find("error") < 0:
            return True, text, model
    return False, "", "cline: free-модели не ответили"


def _try_koda_desktop(prompt: str, timeout: int) -> tuple[bool, str, str]:
    """Koda Desktop / Koda Code — CLI-движок, бесплатные кредиты."""
    import shutil

    koda = shutil.which("koda") or shutil.which("koda.cmd")
    if not koda:
        return False, "", "koda: команда не найдена"
    try:
        proc = subprocess.run(
            [koda, "-p", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return False, "", f"koda: {exc}"
    text = (proc.stdout or "").strip()
    if text and text.lower()[:400].find("error") < 0:
        return True, text, "koda"
    return False, "", "koda: пустой ответ"


def _try_multitool(prompt: str, timeout: int) -> tuple[bool, str, str]:
    import shutil

    mt = shutil.which("multitool") or shutil.which("mt")
    if not mt:
        return False, "", "multitool: команда не найдена"
    try:
        proc = subprocess.run(
            [mt, "run", prompt],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return False, "", f"multitool: {exc}"
    text = (proc.stdout or "").strip()
    if text and text.lower()[:400].find("error") < 0:
        return True, text, "multitool"
    return False, "", "multitool: пустой ответ"


def _try_docker_gordon(prompt: str, timeout: int) -> tuple[bool, str, str]:
    """Gordon (Docker AI) — локальные модели через шим из реестра INVR."""
    root = os.environ.get("INVR_TOOLS_ROOT") or ""
    shim = Path(root) / "registry" / "gordon-chat" / "0.2.0" / "gordon-chat.ps1" if root else None
    if shim is None or not shim.exists():
        return False, "", "gordon: шим не найден"
    pwsh = shutil_which_powershell()
    if not pwsh:
        return False, "", "gordon: Powershell не найден"
    try:
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(shim), "-Ask", prompt, "-TimeoutSec", str(timeout), "-Json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 10,
        )
        out = (proc.stdout or "").strip()
        obj = json.loads(out) if out else {}
        if obj.get("status") == "ok" and obj.get("text"):
            return True, str(obj["text"]), "gordon"
    except Exception:  # noqa: BLE001
        pass
    return False, "", "gordon: нет ответа"


def shutil_which_powershell() -> str:
    import shutil

    return shutil.which("pwsh") or shutil.which("powershell") or ""


PROBES = (
    ("opencode-free", _try_opencode_free),
    ("cline-free", _try_cline_free),
    ("koda-desktop", _try_koda_desktop),
    ("multitool", _try_multitool),
    ("gordon-docker", _try_docker_gordon),
)


def collect_logs(root: str | Path, job_id: str, session_id: str = "") -> list[str]:
    """Собрать хвосты логов для ремонтного пула (не более ~120 строк)."""
    root = Path(root)
    paths = [
        root / "state" / "jobs.jsonl",
        root / "state" / "ledger.jsonl",
        root / "logs",
        root / "state" / "sessions" / f"{session_id}.json" if session_id else None,
    ]
    lines: list[str] = []
    for p in paths:
        if not p or not Path(p).exists():
            continue
        if p.is_dir():
            for f in sorted(p.glob("cascade-answer-*.json"))[-3:]:
                try:
                    lines.append(f"\n--- {f.name} ---")
                    lines += f.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
                except OSError:
                    pass
            continue
        try:
            lines.append(f"\n--- {p.name} ---")
            lines += p.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        except OSError:
            pass
    return lines[-120:]


def repair(settings=None, *, job_id: str = "", goal: str = "",
           session_id: str = "", error: str = "") -> dict:
    """Прогнать задачу через ремонтный пул; вернуть {ok, answer, model, action}."""
    from .paths import repo_root

    root = repo_root(settings)
    logs = collect_logs(root, job_id, session_id)
    prompt = (
        "Фабрика не смогла ответить на запрос оператора штатным каскадом. "
        "Проанализируй приведённые ниже логи ошибок и ВЕРНИ:\n"
        "1) Ответ на исходный запрос (реши своими силами);\n"
        "2) Требование к действию (что нужно сделать фабрике, чтобы в следующий "
        "раз ответить напрямую), если такое есть.\n\n"
        f"ЗАПРОС ОПЕРАТОРА:\n{goal}\n\n"
        f"ОШИБКА ШТАТНОГО КАСКАДА:\n{error[:2000]}\n\n"
        f"ЛОГИ ФАБРИКИ (хвост):\n" + "\n".join(logs[-120:]) + "\n\n"
        "Ответ дай в виде:\nОТВЕТ: <текст ответа>\nДЕЙСТВИЕ: <что исправить>"
    )
    timeout = 240
    attempts: list[dict] = []
    for name, probe in PROBES:
        try:
            ok, text, model = probe(prompt, timeout)
        except Exception as exc:  # noqa: BLE001
            attempts.append({"pool": name, "ok": False, "error": str(exc)})
            continue
        attempts.append({"pool": name, "ok": ok, "model": model or None})
        if ok:
            action = ""
            # вытащить строку «ДЕЙСТВИЕ: …», если модель её вернула
            for line in text.splitlines():
                if line.strip().upper().startswith("ДЕЙСТВИЕ:"):
                    action = line.split(":", 1)[1].strip()[:500]
                    break
            return {
                "ok": True,
                "answer": text[:4000],
                "model": model or name,
                "pool": name,
                "action": action,
                "attempts": attempts,
            }
    return {"ok": False, "error": "ремонтный пул не дал ответа",
            "attempts": attempts}


def write_report(root: Path, result: dict) -> Path | None:
    """Сохранить отчёт ремонта рядом с задачами (state/repair/<ts>.json)."""
    try:
        out = root / "state" / "repair"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None