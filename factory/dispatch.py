"""Диспетчер фабрики: план и исполнение заказ-наряда.

Связывает домены → компетенции → адаптеры (Lab100 / INVR / локальная LLM / регион),
учитывает режим автоматизации (auto|semi), пишет единый журнал и отправляет
метрики/учёт изменений через интеграции (orch-metrics, backup-util) — этап 3.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "capability": self.capability,
            "adapter": self.adapter,
            "action": self.action,
        }


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
            route = CAPABILITY_ROUTE.get(cap)
            if not route:
                continue
            adapter, action = route
            params: dict = {"query": task.goal, "prompt": task.goal}
            if adapter == "lab100":
                provider = str(task.inputs.get("provider")
                               or task.constraints.get("provider") or "")
                if provider:
                    params["provider"] = provider
            steps.append(Step(len(steps), cap, adapter, action, params))
        return matched, steps

    def execute(self, task, approve=None, dry_run: bool = False) -> dict:
        matched, steps = self.plan(task)
        automation = task.automation or self.settings.automation
        checkpoints = list(self.settings.checkpoints)
        task_id = task.id or (task.goal[:60] + "…" if len(task.goal) > 60 else task.goal)

        self.ledger.append({
            "kind": "task.start",
            "task": task_id,
            "automation": automation,
            "domains": [d.name for d in matched],
        })

        results: list[StepResult] = []
        stopped = False
        for step in steps:
            need = automation == "semi" and (
                not checkpoints
                or step.capability in checkpoints
                or step.action in checkpoints
                or step.adapter in checkpoints
            )
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

            adapter = self.adapters.get(step.adapter)
            if adapter is None or not adapter.available():
                res = StepResult(False, "unavailable", step.adapter, step.action,
                                 error="адаптер недоступен")
            else:
                res = adapter.run(step.action, step.params, dry_run=dry_run)
            results.append(res)
            self.ledger.append({
                "kind": "step",
                "task": task_id,
                "step": step.id,
                "capability": step.capability,
                "adapter": step.adapter,
                "action": step.action,
                "status": res.status,
                "ok": res.ok,
                "error": res.error,
            })

        success = not stopped and all(r.ok or r.status == "skipped" for r in results)
        self.ledger.append({"kind": "task.done", "task": task_id, "ok": success,
                            "steps": len(results)})
        self._observe(task, task_id, matched, automation, steps, results, success, dry_run)
        return {
            "task": task.to_dict(),
            "domains": [d.name for d in matched],
            "automation": automation,
            "steps": [s.to_dict() for s in steps],
            "results": [r.to_dict() for r in results],
            "ok": success,
            "stopped": stopped,
        }

    def _observe(self, task, task_id: str, matched, automation: str, steps,
                 results: list[StepResult], success: bool, dry_run: bool) -> None:
        """Метрики (orch-metrics), учёт изменений (backup-util) и квот — этап 3/4.

        Интеграции не должны ломать выполнение задачи, поэтому сбой каждой из
        них изолирован. В dry-run ничего не отправляется и не учитывается.
        """
        if dry_run:
            return
        metrics = (self.integrations or {}).get("metrics")
        if metrics is not None:
            try:
                metrics.emit({
                    "kind": "task", "task": task_id, "ok": success,
                    "steps": len(results), "domains": [d.name for d in matched],
                    "automation": automation,
                })
            except Exception:  # noqa: BLE001 - наблюдаемость не критична
                pass
        self._record_quotas(steps, results)
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

    def _record_quotas(self, steps, results: list[StepResult]) -> None:
        """Зеркальный учёт расхода квот Lab100 (этап 4).

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
                quotas.record(self.settings, provider, 1)
            except Exception:  # noqa: BLE001 - учёт квот не критичен
                pass
