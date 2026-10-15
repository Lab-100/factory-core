"""Диспетчер фабрики: план и исполнение заказ-наряда.

Связывает домены → компетенции → адаптеры (Lab100 / INVR / локальная LLM / регион),
поддерживает вызов инструментов-шимов INVR, реактивную смену региона при
гео-блокировке подходящего шага, учитывает режим автоматизации (auto|semi),
складывает результаты в каталог артефактов и пишет единый журнал, сливая туда
метрики (orch-metrics), квоты (Lab100) и учёт изменений (backup-util) —
этапы 3/4 и §4.5/§5.6/§8.4.
"""
from __future__ import annotations

import json
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import build_adapters
from .adapters.base import StepResult
from .integrations import build_integrations
from .ledger import Ledger
from .settings import Settings

# компетенция -> (адаптер, действие)
CAPABILITY_ROUTE: dict[str, tuple[str, str]] = {
    "web-search": ("lab100", "search"),
    "deep-research": ("lab100", "research"),
    "scrape": ("lab100", "scrape"),
    "code": ("llm_local", "chat"),
    "answer": ("llm_local", "chat"),
    "vision": ("llm_local", "vision"),
    "region-switch": ("region", "switch"),
}


@dataclass
class Step:
    id: int
    capability: str
    adapter: str
    action: str
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    when: str | None = None
    node_id: str | None = None

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "capability": self.capability,
            "adapter": self.adapter,
            "action": self.action,
        }
        if self.node_id:
            data["node_id"] = self.node_id
        if self.depends_on:
            data["depends_on"] = list(self.depends_on)
        if self.when:
            data["when"] = self.when
        return data


@dataclass
class _Outcome:
    """Итог узла DAG: контекст для условий `when` вида `s1.ok` (`_R['s1'].ok`)."""
    ok: bool
    status: str
    error: str = ""

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


class Dispatcher:
    def __init__(self, settings: Settings, domains, ledger: Ledger | None = None, adapters=None,
                 integrations=None):
        self.settings = settings
        self.domains = domains
        self.ledger = ledger or Ledger("state/ledger.jsonl")
        self.adapters = adapters if adapters is not None else build_adapters(settings)
        self.integrations = build_integrations(settings) if integrations is None else integrations

    def plan(self, task):
        matched = self.domains.resolve(task)
        # Явный граф задачи (constraints.dag = [...]) полностью описывает шаги
        spec = self._dag_spec(task)
        if isinstance(spec, list):
            return matched, self._dag_from_spec(task, matched, spec)
        capabilities = list(task.capabilities)
        if not capabilities:
            for domain in matched:
                for cap in domain.capabilities:
                    if cap not in capabilities:
                        capabilities.append(cap)

        steps: list[Step] = []
        allow_region = bool(task.constraints.get("allow_region_switch")) and any(
            d.region == "auto" for d in matched
        )
        if allow_region:
            region = str(
                task.inputs.get("region")
                or task.constraints.get("region")
                or self.settings.get("region.default", "US")
            )
            zone = task.inputs.get("zone") or task.constraints.get("zone")
            params: dict = {"region": region}
            if zone:
                params["zone"] = zone
            steps.append(Step(len(steps), "region-switch", "region", "switch", params))

        for cap in capabilities:
            route = self._route_for(cap)
            if not route:
                continue
            adapter, action = route
            params: dict = {"query": task.goal, "prompt": task.goal}
            if adapter == "lab100":
                provider = str(task.inputs.get("provider")
                               or task.constraints.get("provider") or "")
                if provider:
                    params["provider"] = provider
            if adapter == "external_agent":
                provider = str(task.inputs.get("provider")
                               or task.constraints.get("provider") or "")
                if provider:
                    params["provider"] = provider
            if adapter == "llm_local":
                # vision (§4.5): пути к изображениям и модель — из входов задачи,
                # чтобы адаптер отправил их в Ollama как мультимодальное сообщение
                images = task.inputs.get("images")
                if images is None and task.inputs.get("image"):
                    images = [task.inputs["image"]]
                if isinstance(images, (str, Path)):
                    images = [images]
                if images:
                    params["images"] = [str(item) for item in images]
                if task.inputs.get("model"):
                    params["model"] = str(task.inputs["model"])
                else:
                    preferred = self._domain_llm(matched, cap)
                    if preferred:
                        params["llm"] = preferred
                        if preferred == "ollama" or preferred.startswith("ollama:"):
                            model = (preferred.split(":", 1)[1].strip() if ":" in preferred
                                     else str(self.settings.get(
                                         "adapters.llm_local_model", "") or ""))
                            if model:
                                params["model"] = model
            steps.append(Step(len(steps), cap, adapter, action, params))

        # шаги-инструменты склада INVR (§4.5): шимы вызываются напрямую
        for name, args, timeout in self._task_tools(task, matched):
            params: dict = {"tool": name}
            if args:
                params["args"] = args
            if timeout:
                params["timeout_s"] = timeout
            steps.append(Step(len(steps), f"tool:{name}", "invr", "run", params))
        # DAG-режим без явного графа: обычный план, сшитый в цепочку зависимостей
        if self._dag_requested(task):
            steps = self._chain_dag(steps)
        return matched, steps

    # --- DAG-режим (§4.5): зависимости, условия, топосортировка, параллелизм ---
    @staticmethod
    def _dag_spec(task):
        """Поле `constraints.dag`: True/список шагов — включает граф. Иначе None."""
        return task.constraints.get("dag")

    def _dag_requested(self, task) -> bool:
        """DAG включён полем задачи либо настройкой `dispatch.mode = "dag"`."""
        if bool(self._dag_spec(task)):
            return True
        return str(self.settings.get("dispatch.mode", "") or "").strip().lower() == "dag"

    @staticmethod
    def _chain_dag(steps: list[Step]) -> list[Step]:
        """Сшить линейный план в цепочку: шаг n зависит от шага n-1."""
        prev: str | None = None
        for step in steps:
            step.node_id = step.node_id or f"n{step.id}"
            if prev is not None:
                step.depends_on = [prev]
            prev = step.node_id
        return steps

    def _dag_from_spec(self, task, matched, spec: list) -> list[Step]:
        """Построить шаги из явного описания графа задачи.

        Каждый элемент: ``{id, capability|tool, params?, depends_on?, when?}``.
        Ветвления и условия задаются ссылками ``depends_on``/``when`` на id шагов.
        """
        steps: list[Step] = []
        index: dict[str, Step] = {}
        for item in spec:
            if not isinstance(item, dict):
                raise ValueError("DAG: шаг должен быть объектом {id, capability|tool, ...}")
            node_id = str(item.get("id") or "").strip()
            if not node_id:
                raise ValueError("DAG: у шага не задан id")
            if node_id in index:
                raise ValueError(f"DAG: повторяющийся id шага: {node_id}")
            tool = str(item.get("tool") or "").strip()
            if tool:
                capability = f"tool:{tool}"
                adapter, action = "invr", "run"
                params: dict = {"tool": tool}
                args = item.get("args")
                if args:
                    params["args"] = list(args) if isinstance(args, list) else [args]
                timeout = item.get("timeout_s") or item.get("timeout")
                if timeout:
                    params["timeout_s"] = timeout
            else:
                capability = str(item.get("capability") or "").strip()
                route = self._route_for(capability) if capability else None
                if not route:
                    raise ValueError(f"DAG: неизвестная компетенция: {capability!r}")
                adapter, action = route
                params = {"query": task.goal, "prompt": task.goal}
            extra = item.get("params")
            if isinstance(extra, dict):
                params.update({key: value for key, value in extra.items() if value is not None})
            raw_deps = item.get("depends_on") or []
            if isinstance(raw_deps, str):
                raw_deps = [raw_deps]
            deps = [str(dep).strip() for dep in raw_deps if str(dep).strip()]
            when = str(item.get("when") or "").strip() or None
            step = Step(len(steps), capability, adapter, action, params,
                        depends_on=deps, when=when, node_id=node_id)
            index[node_id] = step
            steps.append(step)
        for step in steps:
            for dep in step.depends_on:
                if dep not in index:
                    raise ValueError(f"DAG: шаг {step.node_id} ссылается на неизвестный шаг {dep}")
        self._dag_levels(steps)   # ранее обнаружит цикл, чем начнётся исполнение
        return steps

    @staticmethod
    def _dag_levels(steps: list[Step]) -> list[list[Step]]:
        """Топологическая сортировка (Kahn) на уровни независимых шагов.

        Уровни — это шаги, которые можно запускать параллельно; все зависимости
        уровня уже выполнены. В порядок входят и явные ``depends_on``, и ссылки
        из условий ``when``: условие читает итог узла, значит узел должен идти
        позже. Цикл зависимостей — ошибка конфигурации задачи.
        """
        index: dict[str, int] = {}
        for position, step in enumerate(steps):
            key = step.node_id or f"n{step.id}"
            if key in index:
                raise ValueError(f"DAG: повторяющийся узел графа: {key}")
            index[key] = position
        count = len(steps)
        deps_of: list[list[str]] = []
        for step in steps:
            deps = list(step.depends_on)
            for ref in Dispatcher._when_refs(step.when):
                if ref in index and ref not in deps:
                    deps.append(ref)
            deps_of.append(deps)
        children: list[list[int]] = [[] for _ in range(count)]
        indegree = [0] * count
        for position, deps in enumerate(deps_of):
            indegree[position] = len(deps)
            for dep in deps:
                if dep not in index:
                    raise ValueError(f"DAG: неизвестная зависимость: {dep}")
                children[index[dep]].append(position)
        queue = deque(position for position in range(count) if indegree[position] == 0)
        levels: list[list[Step]] = []
        seen = 0
        while queue:
            level: list[Step] = []
            for _ in range(len(queue)):
                position = queue.popleft()
                level.append(steps[position])
                seen += 1
                for child in children[position]:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        queue.append(child)
            levels.append(level)
        if seen != count:
            raise ValueError("DAG: обнаружен цикл зависимостей (топологическая сортировка невозможна)")
        return levels

    _WHEN_REF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.-]*)\.(ok|status|skipped|error)\b")

    @staticmethod
    def _when_refs(when: str | None) -> list[str]:
        """Имена узлов, на которые ссылается условие ``when`` (``s1.ok`` → ``s1``)."""
        if not when:
            return []
        return [name for name, _ in Dispatcher._WHEN_REF.findall(when)]

    @classmethod
    def _eval_when(cls, expr: str | None, outcomes: dict[str, "_Outcome"]) -> bool:
        """Проверить условие `when` вроде ``s1.ok and not s2.skipped``.

        Допустимы ссылки ``id.ok|status|skipped|error``, операторы and/or/not и
        скобки. Ошибка разбора или неизвестный id — условие считается ложным.
        """
        if not expr:
            return True
        safe = cls._WHEN_REF.sub(
            lambda m: f"_R[{m.group(1)!r}].{m.group(2)}", expr)
        try:
            return bool(eval(safe, {"__builtins__": {}}, {"_R": outcomes}))  # noqa: S307
        except Exception:  # noqa: BLE001 - условие не должно ронять задачу
            return False

    def _domain_llm(self, matched, capability: str) -> str:
        """Первый LLM-провайдер из поля `llm` домена, покрывающего компетенцию.

        Поле `llm` домена задаёт приоритет провайдеров. Если домен предпочитает
        локальную Ollama, шаг получит её модель (или модель по умолчанию), иначе
        провайдер фиксируется в параметрах шага — это видно в плане и журнале.
        """
        for dom in matched:
            caps = list(getattr(dom, "capabilities", []) or [])
            if capability and caps and capability not in caps:
                continue
            for item in getattr(dom, "llm", []) or []:
                name = str(item).strip()
                if name:
                    return name
        return ""

    def _route_for(self, capability: str) -> tuple[str, str] | None:
        """Маршрут компетенции: настройка `routing.<cap>` или встроенная таблица.

        Настройка перекрывает таблицу: ``routing.answer = "external_agent"`` или
        ``"external_agent:chat"``. При ``providers.auto_route = true`` и наличии
        включённых внешних провайдеров компетенции code/answer/deep-research
        уходят во внешний агент (если маршрут явно не задан).
        """
        override = str(self.settings.get(f"routing.{capability}", "") or "").strip()
        if override:
            if ":" in override:
                adapter, action = override.split(":", 1)
            else:
                builtin = CAPABILITY_ROUTE.get(capability)
                adapter, action = override, (builtin[1] if builtin else "run")
            adapter, action = adapter.strip(), action.strip()
            if adapter:
                return (adapter, action or "run")
        route = CAPABILITY_ROUTE.get(capability)
        if (route and capability in ("code", "answer", "deep-research")
                and self.settings.get("providers.auto_route", False)):
            from . import providers
            if providers.enabled(self.settings):
                return ("external_agent", route[1])
        return route

    @staticmethod
    def _tool_entry(entry) -> tuple[str, list, float | None] | None:
        """Разбор описания инструмента: строка или {name, args, timeout_s}."""
        if isinstance(entry, str):
            name = entry.strip()
            return (name, [], None) if name else None
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not name:
                return None
            args = entry.get("args") or []
            if not isinstance(args, list):
                args = [args]
            timeout = entry.get("timeout_s") or entry.get("timeout")
            return (name, list(args), timeout)
        return None

    def _task_tools(self, task, matched) -> list[tuple[str, list, float | None]]:
        """Инструменты из задачи; при `use_domain_tools` — из подошедших доменов.

        По умолчанию (без явных tools и без флага) инструменты не запускаются:
        в реестре доменов встречаются не-исполняемые ключи (например,
        `firecrawl-key`), поэтому автозапуск — только по явному согласию.
        """
        raw: list = list(getattr(task, "tools", []) or [])
        if not raw:
            raw = list((task.inputs or {}).get("tools") or [])
        if not raw and task.constraints.get("use_domain_tools"):
            raw = [name for dom in matched for name in dom.tools]
        seen: set[str] = set()
        out: list[tuple[str, list, float | None]] = []
        for entry in raw:
            parsed = self._tool_entry(entry)
            if parsed and parsed[0] not in seen:
                seen.add(parsed[0])
                out.append(parsed)
        return out

    def execute(self, task, approve=None, dry_run: bool = False) -> dict:
        matched, steps = self.plan(task)
        automation = task.automation or self.settings.automation
        checkpoints = list(self.settings.checkpoints)
        task_id = task.id or (task.goal[:60] + "…" if len(task.goal) > 60 else task.goal)
        allow_region_switch = bool(task.constraints.get("allow_region_switch")) and any(
            d.region == "auto" for d in matched
        )
        default_region = str(
            task.inputs.get("region") or task.constraints.get("region")
            or self.settings.get("region.default", "US")
        )

        self.ledger.append({
            "kind": "task.start",
            "task": task_id,
            "automation": automation,
            "domains": [d.name for d in matched],
        })

        if any(step.depends_on for step in steps):
            results, stopped = self._execute_dag(
                task_id, steps, approve, automation, checkpoints, dry_run,
                allow_region_switch, default_region)
        else:
            results, stopped = self._execute_linear(
                task_id, steps, approve, automation, checkpoints, dry_run,
                allow_region_switch, default_region)

        success = not stopped and all(r.ok or r.status == "skipped" for r in results)
        checks = self._acceptance_checks(task, steps, results, success)
        summary = {
            "task": task.to_dict(),
            "domains": [d.name for d in matched],
            "automation": automation,
            "mode": "dag" if any(s.depends_on for s in steps) else "linear",
            "steps": [s.to_dict() for s in steps],
            "results": [r.to_dict() for r in results],
            "ok": success,
            "stopped": stopped,
            "acceptance": list(task.acceptance),
            "acceptance_checks": checks,
        }
        # каталог артефактов (§5.6): складываем результат по домену
        catalog: str | None = None
        if not dry_run:
            catalog = self._write_catalog(task, task_id, matched, steps, results, summary)
            if catalog:
                summary["catalog"] = catalog
                # отдельное событие журнала: доступно через `factory ledger --kind catalog`
                self.ledger.append({"kind": "catalog", "task": task_id,
                                    "catalog": catalog})
        self.ledger.append({"kind": "task.done", "task": task_id, "ok": success,
                            "steps": len(results), "catalog": catalog})
        self._observe(task, task_id, matched, automation, steps, results, success, dry_run,
                      catalog=catalog)
        return summary

    # --- исполнение шагов: линейный контур и DAG-контур (§4.5) ---
    def _needs_approval(self, step: Step, automation: str, checkpoints: list) -> bool:
        """Нужно ли подтверждение оператора на шаге (режим semi)."""
        return automation == "semi" and (
            not checkpoints
            or step.capability in checkpoints
            or step.action in checkpoints
            or step.adapter in checkpoints
        )

    def _run_step(self, task_id: str, step: Step, dry_run: bool, allow_region_switch: bool,
                  default_region: str) -> tuple[StepResult, list]:
        """Выполнить один шаг; события журнала возвращаются вызывающему.

        Возврат ``(результат, события)``: события журнала не пишутся напрямую,
        чтобы DAG-контур мог безопасно коммитить их из главного потока.
        """
        events: list = []
        adapter = self.adapters.get(step.adapter)
        if adapter is None:
            res = StepResult(False, "unavailable", step.adapter, step.action,
                             error="адаптер не найден")
        elif dry_run:
            # в dry-run не требуем наличия репозиториев/инструментов:
            # показываем весь план как «simulated», а не как недоступность
            res = adapter.run(step.action, step.params, dry_run=True)
        elif not adapter.available():
            res = StepResult(False, "unavailable", step.adapter, step.action,
                             error="адаптер недоступен")
        else:
            res = adapter.run(step.action, step.params, dry_run=False)
        # реактивная смена региона при гео-блокировке (§4.5) — одна повторная попытка
        if (not dry_run and not res.ok and step.adapter == "lab100"
                and allow_region_switch and not step.params.get("region_retry")
                and self._looks_blocked(res)):
            res = self._retry_after_region(task_id, step, adapter, default_region, res,
                                           events=events)
        return res, events

    def _step_event(self, task_id: str, step: Step, res: StepResult) -> dict:
        return {
            "kind": "step",
            "task": task_id,
            "step": step.id,
            "capability": step.capability,
            "adapter": step.adapter,
            "action": step.action,
            "status": res.status,
            "ok": res.ok,
            "error": res.error,
        }

    def _execute_linear(self, task_id: str, steps, approve, automation: str,
                        checkpoints: list, dry_run: bool, allow_region_switch: bool,
                        default_region: str) -> tuple[list, bool]:
        results: list[StepResult] = []
        stopped = False
        for step in steps:
            need = self._needs_approval(step, automation, checkpoints)
            if need and not stopped:
                if approve is None:
                    res = StepResult(False, "awaiting-approval", step.adapter, step.action,
                                     {"step": step.id})
                    results.append(res)
                    self.ledger.append({"kind": "step", "task": task_id, "step": step.id,
                                        "status": "awaiting-approval"})
                    stopped = True
                    continue
                if not approve(step):
                    res = StepResult(False, "skipped", step.adapter, step.action,
                                     {"step": step.id})
                    results.append(res)
                    self.ledger.append({"kind": "step", "task": task_id, "step": step.id,
                                        "status": "skipped"})
                    continue
            res, events = self._run_step(task_id, step, dry_run, allow_region_switch,
                                         default_region)
            for event in events:
                self.ledger.append(event)
            results.append(res)
            self.ledger.append(self._step_event(task_id, step, res))
        return results, stopped

    @staticmethod
    def _blocked_by_parent(step: Step, outcomes: dict[str, "_Outcome"]) -> bool:
        """Пропускать ли шаг из-за неудачного предка (§4.5).

        Пропуск каскадный: узел не запускается, если любой его предок из
        ``depends_on`` завершился нештатно (``error``/``unavailable``/``skipped``).
        Явное условие ``when`` перекрывает неявную блокировку — так описывают
        обработчики ошибок вида ``when = "not s1.ok"``.
        """
        if step.when:
            return False
        return any(dep in outcomes and not outcomes[dep].ok for dep in step.depends_on)

    def _execute_dag(self, task_id: str, steps, approve, automation: str,
                     checkpoints: list, dry_run: bool, allow_region_switch: bool,
                     default_region: str) -> tuple[list, bool]:
        """Исполнить план уровнями: внутри уровня независимые ветви параллельны."""
        levels = self._dag_levels(steps)
        parallel = max(1, int(self.settings.get("dispatch.parallel", 2) or 1))
        results: list[StepResult] = []
        outcomes: dict[str, _Outcome] = {}
        stopped = False

        for level in levels:
            to_run: list[Step] = []
            for step in level:
                key = step.node_id or f"n{step.id}"
                if stopped:
                    # как и в линейном контуре: после остановки шаги идут без подтверждения
                    to_run.append(step)
                    continue
                if self._blocked_by_parent(step, outcomes):
                    res = StepResult(False, "skipped", step.adapter, step.action,
                                     {"step": step.id, "reason": "parent-failed"})
                    self.ledger.append(self._step_event(task_id, step, res))
                    results.append(res)
                    outcomes[key] = _Outcome(False, "skipped", "parent-failed")
                    continue
                if step.when and not self._eval_when(step.when, outcomes):
                    res = StepResult(False, "skipped", step.adapter, step.action,
                                     {"step": step.id, "reason": "when-condition",
                                      "condition": step.when})
                    self.ledger.append(self._step_event(task_id, step, res))
                    results.append(res)
                    outcomes[key] = _Outcome(False, "skipped", "when-condition")
                    continue
                if self._needs_approval(step, automation, checkpoints):
                    if approve is None:
                        res = StepResult(False, "awaiting-approval", step.adapter,
                                         step.action, {"step": step.id})
                        self.ledger.append(self._step_event(task_id, step, res))
                        results.append(res)
                        outcomes[key] = _Outcome(False, "awaiting-approval")
                        stopped = True
                        continue
                    if not approve(step):
                        res = StepResult(False, "skipped", step.adapter, step.action,
                                         {"step": step.id, "reason": "approval"})
                        self.ledger.append(self._step_event(task_id, step, res))
                        results.append(res)
                        outcomes[key] = _Outcome(False, "skipped", "approval")
                        continue
                to_run.append(step)

            if not to_run:
                continue
            if parallel > 1 and len(to_run) > 1:
                with ThreadPoolExecutor(max_workers=min(parallel, len(to_run))) as pool:
                    executed = list(pool.map(
                        lambda item: self._run_step(task_id, item, dry_run,
                                                    allow_region_switch, default_region),
                        to_run))
            else:
                executed = [self._run_step(task_id, item, dry_run, allow_region_switch,
                                           default_region) for item in to_run]
            for step, (res, events) in zip(to_run, executed):
                key = step.node_id or f"n{step.id}"
                for event in events:
                    self.ledger.append(event)
                results.append(res)
                self.ledger.append(self._step_event(task_id, step, res))
                outcomes[key] = _Outcome(res.ok, res.status, res.error or "")
        return results, stopped

    # --- реактивная смена региона (§4.5) ---
    _BLOCK_MARKERS = (
        "403", "forbidden", "location is not supported",
        "not available in your region", "not available in your country",
        "geo-block", "geoblock", "blocked in your region",
        "unavailable for legal reasons", "region is blocked",
    )

    @classmethod
    def _looks_blocked(cls, res: StepResult) -> bool:
        """Признак гео-блокировки в ошибке шага Lab100 (повод сменить регион)."""
        output = res.output if isinstance(res.output, dict) else {}
        text = " ".join(str(output.get(key) or "") for key in ("stdout", "stderr", "error"))
        text = f"{text} {res.error or ''}".lower()
        return any(marker in text for marker in cls._BLOCK_MARKERS)

    def _retry_after_region(self, task_id: str, step: Step, adapter, region: str,
                            res: StepResult, events: list | None = None) -> StepResult:
        """Сменить регион и повторить шаг один раз (реакция на гео-блок, §4.5).

        События журнала уходят в ``events``, если он передан (DAG-контур собирает
        их в рабочем потоке и коммитит из главного), иначе — напрямую в журнал.
        """
        sink = self.ledger if events is None else events
        region_adapter = self.adapters.get("region")
        if region_adapter is None or not region_adapter.available():
            sink.append({"kind": "region-retry", "task": task_id, "step": step.id,
                         "region": region, "ok": False,
                         "reason": "адаптер region недоступен"})
            return res
        switch = region_adapter.run("switch", {"region": region})
        sink.append({"kind": "region-retry", "task": task_id, "step": step.id,
                     "region": region, "ok": switch.ok, "switch": switch.status})
        if not switch.ok:
            return res
        params = dict(step.params)
        params["region_retry"] = True
        retry = adapter.run(step.action, params, dry_run=False)
        note = {"region_retry": True, "region": region}
        retry.output = {**retry.output, **note} if isinstance(retry.output, dict) else note
        return retry

    # --- критерии приёмки (§4.5): машинная проверка того, что проверяемо ---
    def _acceptance_checks(self, task, steps, results: list[StepResult],
                           success: bool) -> list[dict]:
        """Оценить критерии приёмки задачи там, где это можно сделать машиной.

        Критерий — строка; распознаются префиксы:

          * ``ok`` / ``все шаги успешны`` — все шаги выполнены успешно;
          * ``no-error`` / ``без ошибок`` — нет упавших шагов;
          * ``step:<компетенция>`` — шаг с этой компетенцией выполнен успешно;
          * ``contains:<текст>`` — текст есть в выводе хотя бы одного шага;
          * ``not-contains:<текст>`` — текста нет ни в одном выводе;
          * ``artifact:<путь>`` — файл существует (относительно корня фабрики).

        Остальные критерии не угадываются: помечаются ``ok=None`` («на оператора»).
        """
        def texts() -> list[str]:
            out: list[str] = []
            for res in results:
                if isinstance(res.output, dict):
                    out += [str(v) for v in res.output.values()]
            return out

        checks: list[dict] = []
        for raw in list(getattr(task, "acceptance", []) or []):
            criterion = str(raw).strip()
            low = criterion.lower()
            ok: bool | None = None
            note = "проверяет оператор"
            if low in ("ok", "all-ok", "все шаги успешны", "успех"):
                ok, note = success, ("все шаги успешны" if success else "есть неуспешные шаги")
            elif low in ("no-error", "без ошибок"):
                failed = [r for r in results if not r.ok and r.status != "skipped"]
                ok = not failed
                note = "ошибок нет" if ok else f"неуспешных шагов: {len(failed)}"
            elif low.startswith("step:"):
                cap = criterion.split(":", 1)[1].strip()
                pairs = {(s.adapter, s.action) for s in steps if s.capability == cap}
                hit = bool(pairs) and any(
                    (r.adapter, r.action) in pairs and r.ok for r in results)
                ok = hit
                note = f"успешный шаг '{cap}'" if hit else f"нет успешного шага '{cap}'"
            elif low.startswith("contains:"):
                text = criterion.split(":", 1)[1]
                found = any(text in block for block in texts())
                ok, note = found, ("найдено в выводе" if found else "не найдено в выводе")
            elif low.startswith("not-contains:"):
                text = criterion.split(":", 1)[1]
                bad = any(text in block for block in texts())
                ok, note = (not bad), ("не найдено" if not bad else "найдено, но запрещено")
            elif low.startswith("artifact:"):
                rel = criterion.split(":", 1)[1].strip()
                path = Path(rel)
                target = path if path.is_absolute() else self._base() / path
                ok = target.exists()
                note = f"файл {'есть' if ok else 'не найден'}: {rel}"
            checks.append({"criterion": str(raw), "ok": ok, "note": note})
        return checks

    # --- каталог артефактов (§5.6) ---
    def _base(self) -> Path:
        return Path(self.settings.path).parent.parent

    @staticmethod
    def _slug(text: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(text)).strip("-")
        return cleaned[:80] or "task"

    def _catalog_target(self, task, task_id: str, matched) -> tuple[Path, str]:
        base = self._base()
        rel = str((task.outputs or {}).get("catalog") or "").strip()
        if not rel:
            domain = matched[0].name if matched else "misc"
            rel = f"{self.settings.get('catalog_dir', 'catalog')}/{domain}/{self._slug(task_id)}"
        path = Path(rel)
        return (path if path.is_absolute() else base / path), rel

    def _write_catalog(self, task, task_id: str, matched, steps,
                       results: list[StepResult], summary: dict) -> str | None:
        """Записать task.json/result.json/report.md; сбой не ломает задачу."""
        try:
            target, rel = self._catalog_target(task, task_id, matched)
            target.mkdir(parents=True, exist_ok=True)
            (target / "task.json").write_text(
                json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            (target / "result.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (target / "report.md").write_text(
                self._report(task, matched, steps, results,
                             checks=summary.get("acceptance_checks")),
                encoding="utf-8")
            return rel
        except OSError:
            return None

    def _report(self, task, matched, steps, results: list[StepResult],
                checks: list[dict] | None = None) -> str:
        lines = [f"# {task.goal}", ""]
        if task.operator:
            lines.append(f"- Оператор: {task.operator}")
        if matched:
            lines.append("- Домены: " + ", ".join(d.name for d in matched))
        lines.append(f"- Режим: {task.automation or self.settings.automation}")
        lines.append("")
        lines.append("## Шаги")
        for step, res in zip(steps, results):
            lines.append(f"- [{res.status}] {step.capability} ({step.adapter}:{step.action})")
            output = res.output if isinstance(res.output, dict) else {}
            text = output.get("content") or output.get("stdout") or output.get("echo")
            if text:
                lines.append("  ```")
                lines += ["  " + line for line in str(text).strip().splitlines()[:20]]
                lines.append("  ```")
            if res.error:
                lines.append(f"  ошибка: {res.error}")
        if task.acceptance:
            lines.append("")
            lines.append("## Критерии приёмки")
            marked = {c.get("criterion"): c for c in (checks or [])}
            if not marked:
                lines.append("_автоматически не оцениваются — проверяет оператор_")
            else:
                lines.append("_машинно проверяемые критерии отмечены; «?» — на оператора_")
            for item in task.acceptance:
                check = marked.get(str(item))
                mark = " " if check is None else ("x" if check.get("ok") else
                                                 ("?" if check.get("ok") is None else " "))
                suffix = f" — {check['note']}" if check and check.get("note") else ""
                lines.append(f"- [{mark}] {item}{suffix}")
        return "\n".join(lines) + "\n"

    def _observe(self, task, task_id: str, matched, automation: str, steps,
                 results: list[StepResult], success: bool, dry_run: bool,
                 catalog: str | None = None) -> None:
        """Метрики (orch-metrics), учёт изменений (backup-util) и квот — этап 3/4.

        Интеграции не должны ломать выполнение задачи, поэтому сбой каждой из
        них изолирован. В dry-run ничего не отправляется и не учитывается.
        """
        if dry_run:
            return
        # метрики (§8.4): шлём в orch-metrics и дублируем факт в единый журнал
        metrics = (self.integrations or {}).get("metrics")
        if metrics is not None:
            try:
                sent = metrics.emit({
                    "kind": "task", "task": task_id, "ok": success,
                    "steps": len(results), "domains": [d.name for d in matched],
                    "automation": automation,
                })
                self.ledger.append({
                    "kind": "metrics", "task": task_id,
                    "sink": sent.get("sink"), "ok": sent.get("ok"),
                    "steps": len(results),
                })
            except Exception:  # noqa: BLE001 - наблюдаемость не критична
                pass
        # инфраструктурная карта потока (infra-graph, §4.7)
        infra = (self.integrations or {}).get("infra")
        if infra is not None:
            try:
                infra.emit({
                    "kind": "flow", "task": task_id, "ok": success,
                    "domains": [d.name for d in matched],
                    "adapters": sorted({s.adapter for s in steps}),
                    "tools": [s.params.get("tool") for s in steps if s.adapter == "invr"],
                    "steps": len(results),
                })
            except Exception:  # noqa: BLE001 - карта потока не критична
                pass
        self._record_quotas(task_id, steps, results)
        on_change = (getattr(task, "on_change", "") or task.constraints.get("on_change") or "").strip()
        if not on_change:
            return
        backup = (self.integrations or {}).get("backup")
        if backup is None:
            return
        try:
            outputs = task.outputs or {}
            paths = list(outputs.get("artifacts") or [])
            if outputs.get("catalog"):
                paths.append(outputs["catalog"])
            if catalog:
                paths.append(catalog)
            change = backup.record(paths, reason=f"task {task_id} ({on_change})", task=task_id)
            self.ledger.append({"kind": "change", "task": task_id, **change})
        except Exception as exc:  # noqa: BLE001 - учёт изменений не критичен
            self.ledger.append({"kind": "change", "task": task_id, "ok": False, "error": str(exc)})

    @staticmethod
    def _provider_from_result(res: StepResult) -> str:
        """Провайдер, реально использованный Lab100 (строка «источник: …»)."""
        output = res.output if isinstance(res.output, dict) else {}
        stdout = str(output.get("stdout") or "")
        for line in stdout.splitlines():
            if "источник:" not in line:
                continue
            value = line.rsplit("источник:", 1)[1].strip().split()[0]
            if value and value != "-":
                return value
        return ""

    def _record_quotas(self, task_id: str, steps, results: list[StepResult]) -> None:
        """Зеркальный учёт расхода квот Lab100 (этап 4) + запись в журнал (§8.4).

        Считаем только успешно выполненные шаги Lab100. Провайдер берём из
        явного параметра задачи, а иначе — из фактического вывода Lab100
        («источник: …»), чтобы не навязывать автовыбор исполнителя.
        Сбой учёта изолирован.
        """
        from . import quotas

        for step, res in zip(steps, results):
            if step.adapter != "lab100" or not res.ok:
                continue
            provider = str(step.params.get("provider") or "") or self._provider_from_result(res)
            if not provider:
                continue
            try:
                used = quotas.record(self.settings, provider, 1)
                self.ledger.append({"kind": "quota", "task": task_id,
                                    "provider": provider, "amount": 1, "used": used})
            except Exception:  # noqa: BLE001 - учёт квот не критичен
                pass
