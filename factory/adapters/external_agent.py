"""Адаптер внешнего ИИ-агента — встроенные ассистенты сред разработки.

Запускает CLI включённого провайдера (OpenCode, Claude Code, Cursor, Windsurf,
Void, Zen, workAI, MultiTool) и возвращает его ответ. Провайдер берётся из
параметра шага `provider` или из первого включённого; в dry-run команда не
запускается. Провайдеры включаются переключателем (`factory providers enable
<имя>` или раздел «Провайдеры» в дашборде).
"""
from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

_IS_WINDOWS = sys.platform.startswith("win")

from .. import providers
from .base import Adapter, StepResult


class ExternalAgentAdapter(Adapter):
    name = "external_agent"

    def available(self) -> bool:
        return bool(providers.enabled(self.settings))

    def health(self) -> dict:
        items = providers.discover(self.settings)
        return {
            "adapter": self.name,
            "available": self.available(),
            "enabled": [item["name"] for item in items if item["enabled"]],
            "found": [item["name"] for item in items if item["available"]],
        }

    def _pick(self, params: dict) -> dict | None:
        wanted = str(params.get("provider") or "").strip()
        candidates = providers.enabled(self.settings)
        if wanted:
            return next((item for item in candidates if item["name"] == wanted), None)
        # выбор по рейтингу: точность/скорость/специализация/запросы (ratings)
        try:
            from .. import ratings
            from .. import quotas as _quotas
            q_snap = None
            try:
                q_snap = _quotas.build(self.settings).get("providers")
            except Exception:  # noqa: BLE001
                q_snap = None
            chosen = ratings.pick(self.settings, str(params.get("cap") or params.get("action") or "answer"),
                                  candidates, quotas_snapshot=q_snap)
            if chosen is not None:
                return chosen
        except Exception:  # noqa: BLE001 — сбой рейтинга не должен ломать выбор
            pass
        return candidates[0] if candidates else None

    @staticmethod
    def _popen_kwargs() -> dict:
        """Скрытый запуск дочернего CLI на Windows: консольное окно не
        мелькает и не крадёт фокус у пользователя (CREATE_NO_WINDOW +
        STARTF_USESHOWWINDOW/SW_HIDE). На других ОС — пусто."""
        try:
            if _IS_WINDOWS:
                return {"creationflags": getattr(
                    subprocess, "CREATE_NO_WINDOW", 0)}
        except Exception:  # noqa: BLE001
            pass
        return {}

    @staticmethod
    def _execute(command: list, prompt: str, prompt_arg: bool, timeout: int,
                 stream_file: str = "") -> tuple[int, str]:
        cmd = [*command, prompt] if prompt_arg else command
        try:
            kwargs: dict = {"capture_output": True, "text": True,
                            "timeout": timeout, "encoding": "utf-8",
                            "errors": "replace"}
            kwargs.update(ExternalAgentAdapter._popen_kwargs())
            # поток размышлений: путь к файлу порций передаётся мосту каскадом
            # через env (CASCADE_STREAM_FILE), чтобы чат показывал прогресс.
            if stream_file:
                import os as _os
                env = dict(_os.environ)
                env["CASCADE_STREAM_FILE"] = stream_file
                kwargs["env"] = env
            if prompt_arg:
                proc = subprocess.run(cmd, **kwargs)
            else:
                proc = subprocess.run(cmd, input=prompt, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return -1, str(exc)
        return proc.returncode, (proc.stdout or "")

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:
        provider = self._pick(params)
        prompt = str(params.get("prompt") or params.get("query") or "")
        if provider is None:
            return StepResult(False, "unavailable", self.name, action,
                              error="нет включённых внешних провайдеров")
        synthesize = bool(params.get("synthesize"))
        variants = providers.run_commands(self.settings, provider["name"])
        if dry_run:
            return StepResult(True, "simulated", self.name, action,
                              {"provider": provider["name"],
                               "command": variants[0] if variants else [],
                               "commands": variants,
                               "synthesize": synthesize,
                               "prompt": prompt[:200]})
        if not variants:
            return StepResult(False, "unavailable", self.name, action,
                              {"provider": provider["name"]},
                              error="команда провайдера не найдена")
        timeout = int(params.get("timeout_s", 180))
        prompt_arg = bool(provider.get("prompt_arg"))
        # Файл «потока размышлений»: каскад пишет туда порции/этапы, чат
        # показывает их разворачиваемым блоком, не дожидаясь конца обработки.
        stream_file = str(params.get("stream_file") or "").strip()

        if not synthesize:
            return self._run_single(provider["name"], variants, prompt,
                                    prompt_arg, timeout, action, stream_file)
        # Синтез: опросить несколько исполнителей параллельно и свести ответы оркестратором.
        synth_timeout = int(params.get("synth_timeout_s", 60))
        names = [p["name"] for p in providers.enabled(self.settings)]
        wanted = int(params.get("synthesize_count", 3))
        answers: dict[str, str] = {}
        tried = 0

        def _probe(pname: str) -> tuple[str, str] | None:
            p_provider = next((p for p in providers.enabled(self.settings)
                               if p["name"] == pname), None)
            p_arg = bool(p_provider.get("prompt_arg")) if p_provider else False
            for cmd in providers.run_commands(self.settings, pname):
                rc, raw = self._execute(cmd, prompt, p_arg, synth_timeout, stream_file)
                if rc == 0:
                    content = (providers.extract_cline_text(raw)
                               if pname == "cline" else raw.strip())
                    if content:
                        return pname, content[:3000]
            return None

        with ThreadPoolExecutor(max_workers=max(2, min(4, len(names)))) as pool:
            futures = [pool.submit(_probe, pn) for pn in names]
            for fut in as_completed(futures):
                if len(answers) >= wanted:
                    break
                try:
                    got = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                if got and got[0] not in answers:
                    answers[got[0]] = got[1]
                    tried += 1
        if not answers:
            return StepResult(False, "error", self.name, action,
                              {"provider": provider["name"], "tried": tried},
                              error="все исполнители не дали ответа для синтеза")
        parts = "\n\n".join(f"Ответ нейросети {i + 1} ({name}):\n{text}"
                            for i, (name, text) in enumerate(answers.items()))
        synth_prompt = (
            "Ты — оркестратор. Несколько нейросетей ответили на один вопрос. "
            "Проанализируй их ответы, отбрось ошибки и повторы, и дай ЕДИНЫЙ "
            "итоговый ответ на исходный вопрос. Вопрос:\n"
            f"{prompt}\n\n{parts}"
        )
        orchestrator = variants[0]
        max_rounds = int(params.get("synth_rounds", 3))

        def _assess(text: str) -> str:
            """Оценка ответа: 'ok' | 'weak' | 'bad' по содержанию."""
            t = (text or "").strip()
            if len(t) < 15:
                return "bad"
            low = t.lower()
            bad_markers = ("не знаю", "не могу ответить", "нет данных",
                           "i don't know", "error", "ошибка", "извини",
                           "нет информации", "не нашёл")
            for m in bad_markers:
                if m in low:
                    return "bad"
            # ответ почти повторяет вопрос — считаем слабым
            q = (prompt or "").strip().lower()[:40]
            if q and q in low[:len(q) + 5]:
                return "weak"
            return "ok"

        last_content = ""
        errors: list[str] = []
        # Круг доводки: если синтезированный ответ слабый/пустой/расходится —
        # уточняем оркестратору (с замечанием) и пробуем снова, до max_rounds.
        for round_i in range(1, max_rounds + 1):
            rc, raw = self._execute(orchestrator, synth_prompt, prompt_arg, timeout)
            if rc != 0:
                errors.append((raw or "")[:200] or f"код {rc}")
                # дали оркестратору сбойную попытку — не тратим раунд впустую
                if round_i >= max_rounds:
                    break
                synth_prompt += (
                    f"\n\nПримечание: предыдущая попытка синтеза сбояла "
                    f"({(raw or '')[:120]}). Попробуй ещё раз кратко и прямо."
                )
                continue
            content = (providers.extract_cline_text(raw)
                       if provider["name"] == "cline" else raw.strip())
            last_content = content
            verdict = _assess(content)
            if verdict == "ok":
                return StepResult(True, "ok", self.name, action,
                                  {"provider": provider["name"], "synthesized": True,
                                   "sources": list(answers.keys()),
                                   "content": content[:4000], "tried": tried,
                                   "rounds": round_i})
            if verdict == "weak":
                synth_prompt += (
                    "\n\nПримечание: твой прежний ответ почти повторял вопрос и "
                    "не отвечал по сути. Дай развёрнутый и по делу ответ на вопрос."
                )
            else:  # bad
                synth_prompt += (
                    "\n\nПримечание: предыдущий ответ был неудовлетворительным "
                    "(пустой, «не знаю» или сбой). Дай честный, корректный "
                    "ответ на исходный вопрос."
                )
        # после всех раундов: если хоть что-то есть — отдаём это, иначе отказ
        if last_content:
            return StepResult(True, "ok", self.name, action,
                              {"provider": provider["name"], "synthesized": True,
                               "sources": list(answers.keys()),
                               "content": last_content[:4000], "tried": tried,
                               "rounds": max_rounds, "final_fallback": True})
        return StepResult(False, "error", self.name, action,
                          {"provider": provider["name"], "tried": tried,
                           "rounds": max_rounds},
                          error=("; ".join(errors) or "оркестратор не дал внятного "
                                 "ответа после нескольких раундов доводки"))

    def _run_single(self, pname: str, variants: list, prompt: str,
                    prompt_arg: bool, timeout: int, action: str,
                    stream_file: str = "") -> StepResult:
        import time as _time
        last_error = ""
        last_code = None
        tried = 0
        started = _time.time()
        for command in variants:
            tried += 1
            rc, raw = self._execute(command, prompt, prompt_arg, timeout, stream_file)
            if rc == 0:
                content = (providers.extract_cline_text(raw)
                           if pname == "cline" else raw.strip())
                if content:
                    # запись статистики в рейтинг (точность/скорость/специализация)
                    try:
                        from .. import ratings
                        ratings.record(self.settings, pname, str(action), True,
                                       (_time.time() - started) * 1000.0)
                    except Exception:  # noqa: BLE001
                        pass
                    return StepResult(True, "ok", self.name, action,
                                      {"provider": pname,
                                       "content": content[:4000],
                                       "tried": tried})
                last_error = "пустой ответ"
                last_code = rc
                continue
            last_error = raw.strip()[:500] or f"код возврата {rc}"
            last_code = rc
        # отказ — фиксируем ошибку в статистике
        try:
            from .. import ratings
            ratings.record(self.settings, pname, str(action), False,
                           (_time.time() - started) * 1000.0)
        except Exception:  # noqa: BLE001
            pass
        return StepResult(False, "error", self.name, action,
                          {"provider": pname, "returncode": last_code,
                           "tried": tried},
                          error=last_error or "все модели не сработали")
