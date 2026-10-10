"""Подвижные (динамические) домены.

Домены описываются в config/domains.toml и накладываются профилем
(config/profiles/*.toml). Меняются без правки кода: включение/выключение,
приоритет, свои домены. Поддерживается горячая перезагрузка (reload) и
подбор домена под задачу (explicit → keywords → capabilities).
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import tomlw


@dataclass
class Domain:
    name: str
    title: str = ""
    enabled: bool = True
    priority: int = 50
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    llm: list[str] = field(default_factory=list)
    policy: str = "local-first"
    region: str = "auto"
    keywords: list[str] = field(default_factory=list)
    source: str = "base"  # base | override | add | runtime

    @classmethod
    def from_dict(cls, name: str, data: dict, source: str = "base") -> "Domain":
        return cls(
            name=name,
            title=str(data.get("title", "")),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 50)),
            capabilities=list(data.get("capabilities", []) or []),
            tools=list(data.get("tools", []) or []),
            llm=list(data.get("llm", []) or []),
            policy=str(data.get("policy", "local-first")),
            region=str(data.get("region", "auto")),
            keywords=list(data.get("keywords", []) or []),
            source=source,
        )

    def patch(self, data: dict, source: str = "override") -> "Domain":
        updates: dict = {"source": source}
        for key in ("title", "enabled", "priority", "capabilities", "tools",
                    "llm", "policy", "region", "keywords"):
            if key in data:
                updates[key] = data[key]
        return replace(self, **updates)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "enabled": self.enabled,
            "priority": self.priority,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "llm": self.llm,
            "policy": self.policy,
            "region": self.region,
            "keywords": self.keywords,
        }


class DomainRegistry:
    def __init__(self, base_path: str | Path, overlay_paths: list[str | Path] | None = None):
        self.base_path = Path(base_path)
        self.overlay_paths = [Path(p) for p in (overlay_paths or [])]
        self.match_by = "auto"
        self.domains: dict[str, Domain] = {}
        self._runtime_changes: dict[str, dict] = {}
        self.reload()

    # --- загрузка ---
    def reload(self) -> None:
        data = self._read(self.base_path)
        self.match_by = str(data.get("match_by", "auto"))
        domains: dict[str, Domain] = {}
        for name, raw in (data.get("domain", {}) or {}).items():
            domains[name] = Domain.from_dict(name, raw, source="base")
        for overlay in self.overlay_paths:
            if not overlay.exists():
                continue
            odata = self._read(overlay)
            for name, patch in (odata.get("override", {}) or {}).items():
                if name in domains:
                    domains[name] = domains[name].patch(patch, source="override")
            for name, raw in (odata.get("add", {}) or {}).items():
                domains[name] = Domain.from_dict(name, raw, source="add")
        # рантайм-правки поверх всего
        for name, patch in self._runtime_changes.items():
            if name in domains:
                domains[name] = domains[name].patch(patch, source="runtime")
        self.domains = domains

    def _read(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with path.open("rb") as handle:
            return tomllib.load(handle)

    # --- доступ ---
    def all(self) -> list[Domain]:
        return sorted(self.domains.values(), key=lambda d: d.priority, reverse=True)

    def enabled(self) -> list[Domain]:
        return [d for d in self.all() if d.enabled]

    def get(self, name: str) -> Domain | None:
        return self.domains.get(name)

    # --- изменения в рантайме ---
    def set_enabled(self, name: str, enabled: bool) -> Domain:
        if name not in self.domains:
            raise KeyError(f"домен не найден: {name}")
        self._runtime_changes.setdefault(name, {})["enabled"] = bool(enabled)
        self.reload()
        return self.domains[name]

    def set_priority(self, name: str, priority: int) -> Domain:
        if name not in self.domains:
            raise KeyError(f"домен не найден: {name}")
        self._runtime_changes.setdefault(name, {})["priority"] = int(priority)
        self.reload()
        return self.domains[name]

    def persist_overlay(self, path: str | Path) -> None:
        overlay = {"schema": "invr.factory.profile/1", "name": "runtime", "override": {}}
        for name, patch in self._runtime_changes.items():
            overlay["override"][name] = patch
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(tomlw.dumps(overlay), encoding="utf-8")

    # --- подбор домена под задачу ---
    def resolve(self, task) -> list[Domain]:
        candidates = self.enabled()
        if task.domain:
            dom = self.get(task.domain)
            return [dom] if dom and dom.enabled else []
        mode = self.match_by
        text = (task.goal or "") + " " + json.dumps(task.inputs or {}, ensure_ascii=False)
        text = text.lower()
        if mode in ("auto", "keywords"):
            scored = []
            for dom in candidates:
                hits = sum(1 for kw in dom.keywords if kw.lower() in text)
                if hits:
                    scored.append((hits, dom.priority, dom))
            if scored:
                scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
                return [dom for _, _, dom in scored]
        if mode in ("auto", "capabilities"):
            caps = set(task.capabilities or [])
            by_cap = [d for d in candidates if caps & set(d.capabilities)]
            by_cap.sort(key=lambda d: (len(caps & set(d.capabilities)), d.priority), reverse=True)
            if by_cap:
                return by_cap
        return candidates[:1]
