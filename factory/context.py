"""Сборка рабочего контекста фабрики (настройки + домены + журнал)."""
from __future__ import annotations

from pathlib import Path

from .domains import DomainRegistry
from .ledger import Ledger
from .paths import repo_root
from .settings import load as load_settings


def build_registry(root: Path, settings) -> DomainRegistry:
    domains_file = root / str(settings.get("domains_file", "config/domains.toml"))
    profiles_dir = root / str(settings.get("profiles_dir", "config/profiles"))
    active = str(settings.get("active_profile", "default"))
    overlays: list[Path] = []
    profile = profiles_dir / f"{active}.toml"
    if profile.exists():
        overlays.append(profile)
    runtime = profiles_dir / "_runtime.toml"
    if runtime.exists():
        overlays.append(runtime)
    return DomainRegistry(domains_file, overlays)


def build_context(project: bool = True, start=None):
    root = repo_root()
    settings = load_settings(root / "config" / "settings.toml")
    if project:
        from .projects import apply_project
        apply_project(settings, start=start)
    registry = build_registry(root, settings)
    ledger = Ledger(root / str(settings.get("ledger_path", "state/ledger.jsonl")))
    return root, settings, registry, ledger
