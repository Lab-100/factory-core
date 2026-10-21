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


def _repair_session(root: Path) -> dict:
    """Получить (создав) ремонтную сессию — отдельную от пользовательского чата.

    Сессия имеет kind='repair': она видна в отдельном разделе «Ремонтные
    сессии», но не попадает в обычный список чатов. Заголовок — по времени.
    """
    from .sessions import SessionStore

    store = SessionStore(root)
    now = datetime.now().strftime("%d.%m %H:%M")
    existing = [s for s in store.list(kind="repair")]
    if existing:
        # берём самую свежую ремонтную сессию (обычно одна идёт)
        return store.ensure(existing[0]["id"], title=f"Ремонт {now}", kind="repair")
    return store.create(title=f"Ремонт {now}", kind="repair")


def _note(root: Path, role: str, text: str) -> None:
    """Записать сообщение в ремонтную сессию (не в пользовательский чат)."""
    from .sessions import SessionStore

    try:
        s = _repair_session(root)
        SessionStore(root).append(s["id"], role, text)
    except Exception:  # noqa: BLE001
        pass


def repair(settings=None, *, job_id: str = "", goal: str = "",
           session_id: str = "", error: str = "") -> dict:
    """Прогнать задачу через ремонтный пул; вернуть {ok, answer, model, action}.

    Весь ход ремонта пишется в отдельную ремонтную сессию (kind='repair'),
    скрытую из обычного списка чатов, но видимую в разделе «Ремонтные сессии».
    """
    from .paths import repo_root

    root = repo_root(settings)
    store = from_sessions_store(root)
    repair_ses = _repair_session(root)
    rsid = repair_ses["id"]
    store.append(rsid, "system",
                 f"🔧 ремонт запущен: задача {job_id or '—'}\nзапрос: {goal[:120]}\n"
                 f"ошибка: {error[:300]}")
    store.append(rsid, "system", "→ отправлено в ремонтный пул (вдумчивые бесплатные ИИ): "
                                 "opencode-free, cline-free, koda-desktop, multitool, gordon-docker")

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
            store.append(rsid, "system", f"  ✗ {name}: {exc}")
            continue
        attempts.append({"pool": name, "ok": ok, "model": model or None})
        if ok:
            action = ""
            # вытащить строку «ДЕЙСТВИЕ: …», если модель её вернула
            for line in text.splitlines():
                if line.strip().upper().startswith("ДЕЙСТВИЕ:"):
                    action = line.split(":", 1)[1].strip()[:500]
                    break
            store.append(rsid, "assistant",
                         f"✅ ремонт: пул {name} (модель {model or '—'}) дал ответ.\n"
                         f"ДЕЙСТВИЕ: {action or '—'}\n\n{text[:1500]}")
            return {
                "ok": True,
                "answer": text[:4000],
                "model": model or name,
                "pool": name,
                "action": action,
                "attempts": attempts,
            }
        store.append(rsid, "system", f"  ✗ {name}: не ответил")
    store.append(rsid, "system", "❌ ремонтный пул не дал ответа")
    return {"ok": False, "error": "ремонтный пул не дал ответа",
            "attempts": attempts}


def from_sessions_store(root: Path):
    """Кэш SessionStore в рамках модуля (удобно для _repair_session и repair)."""
    from .sessions import SessionStore

    return SessionStore(root)


def _run_tool(argv: list[str], root: Path, timeout: float = 900,
              env_extra: dict | None = None) -> tuple[int, str]:
    """Запустить команду (тесты/коммит/пуш/релиз) и вернуть (код, вывод)."""
    import shutil as _shutil

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    shell = _shutil.which("pwsh") or _shutil.which("powershell")
    cmd = argv[0]
    if any(x in argv[0].lower() for x in ("factory", "py", "python")):
        if _shutil.which(cmd):
            pass
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=env)
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode, out[-3000:]
    except subprocess.TimeoutExpired:
        return -1, f"таймаут {timeout} с"
    except FileNotFoundError:
        return -2, f"команда не найдена: {argv[0]}"
    except Exception as exc:  # noqa: BLE001
        return -3, str(exc)


def _repo_root(root: Path) -> Path:
    """Корень git-репозитория factory-core (там, где .git рядом)."""
    for cand in (root.parent.parent, root):
        if (cand / ".git").exists():
            return cand
    return root


def run_repair_cycle(settings=None, *, job_id: str = "", goal: str = "",
                     session_id: str = "", error: str = "",
                     publish: bool = True) -> dict:
    """Полный ремонтный цикл: починка + тесты + коммит/пуш (+ релиз).

    Ход пишется в ремонтную сессию (kind='repair'): запуск, перебор пула,
    найденные дефекты, результат тестов, публикация в GitHub.
    """
    from .paths import repo_root

    root = repo_root(settings)
    store = from_sessions_store(root)
    repair_ses = _repair_session(root)
    rsid = repair_ses["id"]

    # 1) Починка через пул вдумчивых ИИ
    result = repair(settings, job_id=job_id, goal=goal,
                    session_id=session_id, error=error)

    # 2) Тесты рабочей копии (проверка работоспособности)
    store.append(rsid, "system", "🧪 запуск тестов (проверка работоспособности)…")
    repo = _repo_root(root)
    py = sys.executable
    rc, out = _run_tool([py, "-m", "unittest", "discover", "-s", "tests"],
                        repo, timeout=900)
    defects = []
    if rc == 0:
        store.append(rsid, "system", "✅ тесты зелёные.")
        result["tests"] = {"ok": True, "output": out[-1200:]}
    else:
        lines = out.splitlines()
        defects.append("тесты падают (см. вывод ниже)")
        store.append(rsid, "system",
                     f"❌ тесты ПАДАЮТ (код {rc}):\n{out[-1200:]}")
        result["tests"] = {"ok": False, "output": out[-1200:]}
        result["defects"] = defects

    # 3) Публикация исправлений (коммит/пуш/релиз), если есть незакоммиченное
    published = None
    if publish and repo.exists():
        gitrc, gitout = _run_tool(["git", "status", "--porcelain"], repo, timeout=60)
        if gitrc == 0 and gitout.strip():
            store.append(rsid, "system", "📤 есть незакоммиченные правки — коммичу/пушу.")
            store.append(rsid, "system", f"изменённые файлы:\n{gitout.strip()[:1500]}")
            msg = f"factory repair: исправления после отказа задачи {job_id or '—'}"
            _run_tool(["git", "add", "-A"], repo, timeout=120)
            rc2, out2 = _run_tool(["git", "commit", "-m", msg], repo, timeout=120)
            if rc2 == 0:
                store.append(rsid, "system", f"✅ коммит: {msg}")
                rc3, out3 = _run_tool(["git", "push", "origin", "main"], repo, timeout=300)
                if rc3 == 0:
                    store.append(rsid, "system", "✅ push в origin/main выполнен.")
                    # Релиз — только если это настоящие исправления фабрики
                    rc4, out4 = _run_tool(
                        [py, "-m", "factory", "release"],
                        repo, timeout=1200,
                        env_extra={"FACTORY_RELEASE_REPO": "Lab-100/factory-core"})
                    if rc4 == 0:
                        store.append(rsid, "system",
                                     f"🚀 релиз создан:\n{out4[-800:]}")
                        published = {"release": out4[-800:]}
                    else:
                        store.append(rsid, "system",
                                     f"⚠️ релиз не создан (код {rc4}):\n{out4[-800:]}")
                        published = {"release_error": out4[-800:]}
                else:
                    store.append(rsid, "system", f"⚠️ push не удался:\n{out3[-800:]}")
            else:
                store.append(rsid, "system", f"⚠️ коммит не удался:\n{out2[-800:]}")
        else:
            store.append(rsid, "system", "ℹ️ нет незакоммиченных правок — публикация не требуется.")
        result["publish"] = published or {"skipped": True}

    # Итог в ремонтную сессию
    final = "✅ ремонт завершён" if result.get("ok") else "❌ ремонт не дал ответа"
    if result.get("answer"):
        store.append(rsid, "assistant",
                     f"отчёт: {final}\n\n{result['answer'][:2000]}")
    else:
        store.append(rsid, "assistant", f"отчёт: {final}")
    result["repair_session"] = rsid
    return result


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