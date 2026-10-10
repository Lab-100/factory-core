"""Заказ-наряд оператора (invr.task/1)."""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "invr.task/1"


@dataclass
class Task:
    goal: str
    schema: str = SCHEMA
    id: str = ""
    operator: str = ""
    domain: str | None = None
    capabilities: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    automation: str | None = None
    acceptance: list[str] = field(default_factory=list)
    on_change: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        schema = data.get("schema", SCHEMA)
        if schema != SCHEMA:
            raise ValueError(f"неподдерживаемая схема задачи: {schema}")
        if not data.get("goal"):
            raise ValueError("в задаче обязательно поле goal")
        return cls(
            goal=str(data["goal"]),
            schema=schema,
            id=str(data.get("id", "")),
            operator=str(data.get("operator", "")),
            domain=data.get("domain") or None,
            capabilities=list(data.get("capabilities", []) or []),
            inputs=dict(data.get("inputs", {}) or {}),
            outputs=dict(data.get("outputs", {}) or {}),
            constraints=dict(data.get("constraints", {}) or {}),
            automation=data.get("automation"),
            acceptance=list(data.get("acceptance", []) or []),
            on_change=str(data.get("on_change", "") or ""),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Task":
        path = Path(path)
        if path.suffix.lower() == ".toml":
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "id": self.id,
            "operator": self.operator,
            "domain": self.domain,
            "goal": self.goal,
            "capabilities": self.capabilities,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "constraints": self.constraints,
            "automation": self.automation,
            "acceptance": self.acceptance,
            "on_change": self.on_change,
        }
