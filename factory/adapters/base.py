"""Базовый адаптер исполнителя."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepResult:
    ok: bool
    status: str                 # ok | simulated | skipped | awaiting-approval | unavailable | error | noop
    adapter: str
    action: str
    output: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "adapter": self.adapter,
            "action": self.action,
            "output": self.output,
            "error": self.error,
        }


class Adapter:
    name = "base"

    def __init__(self, settings):
        self.settings = settings

    def available(self) -> bool:  # pragma: no cover - переопределяется
        return True

    def run(self, action: str, params: dict, dry_run: bool = False) -> StepResult:  # pragma: no cover
        raise NotImplementedError
