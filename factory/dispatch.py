"""Диспетчер фабрики: план и исполнение заказ-наряда.

Связывает домены → компетенции → адаптеры (Lab100 / INVR / локальная LLM / регион),
поддерживает вызов инструментов-шимов INVR, учитывает режим автоматизации
(auto|semi), складывает результаты в каталог артефактов и пишет единый журнал,
сливая туда метрики (orch-metrics), квоты (Lab100) и учёт изменений
(backup-util) — этапы 3/4 и §4.5/§5.6/§8.4.
"""
from __future__ import annotations

import json
import re
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

        # шаги-инструменты склада INVR (§4.5): шимы вызываются напрямую
        for name, args, timeout in self._task_tools(task, matched):
            params: dict = {"tool": name}
            if args:
                params["args"] = args
            if timeout:
                params["timeout_s"] = timeout
            steps.append(Step(len(steps), f"tool:{name}", "invr", "run", params))
        return matched, steps

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
        summary = {
            "task": task.to_dict(),
            "domains": [d.name for d in matched],
            "automation": automation,
            "steps": [s.to_dict() for s in steps],
            "results": [r.to_dict() for r in results],
            "ok": success,
            "stopped": stopped,
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
            (target / "report.md").write_text(self._report(task, matched, steps, results),
                                              encoding="utf-8")
            return rel
        except OSError:
            return None

    def _report(self, task, matched, steps, results: list[StepResult]) -> str:
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
