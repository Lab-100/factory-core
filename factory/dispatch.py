"""Диспетчер фабрики: план и исполнение заказ-наряда.

Связывает домены → компетенции → адаптеры (Lab100 / INVR / локальная LLM / регион),
учитывает режим автоматизации (auto|semi), пишет единый журнал.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .adapters import build_adapters
from .adapters.base import StepResult
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
    def __init__(self, settings: Settings, domains, ledger: Ledger | None = None, adapters=None):
        self.settings = settings
        self.domains = domains
        self.ledger = ledger or Ledger("state/ledger.jsonl")
        self.adapters = adapters if adapters is not None else build_adapters(settings)

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
            steps.append(Step(len(steps), "region-switch", "region", "switch", {"region": "US"}))

        for cap in capabilities:
            route = CAPABILITY_ROUTE.get(cap)
            if not route:
                continue
            adapter, action = route
            steps.append(Step(len(steps), cap, adapter, action,
                              {"query": task.goal, "prompt": task.goal}))
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
        return {
            "task": task.to_dict(),
            "domains": [d.name for d in matched],
            "automation": automation,
            "steps": [s.to_dict() for s in steps],
            "results": [r.to_dict() for r in results],
            "ok": success,
            "stopped": stopped,
        }
