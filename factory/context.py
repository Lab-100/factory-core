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


def access_summary(settings=None, root: Path | None = None) -> dict:
    """Сводка доступов модели к логам фабрики и провайдеров (приказ владельца).

    Владелец фабрики дал модели право читать все логи; сводка подставляется
    в контекст сессии, чтобы модель знала пути и не отвечала «нет доступа».
    """
    root = root or repo_root()
    settings = settings or load_settings(root / "config" / "settings.toml")
    from pathlib import Path as _Path

    home = _Path.home()

    def _exists(path: str) -> bool:
        try:
            return _Path(path).is_file()
        except OSError:
            return False

    log_paths = {
        "ledger": str(root / "state" / "ledger.jsonl"),
        "jobs": str(root / "state" / "jobs.jsonl"),
        "watchdog": str(root / "state" / "watchdog.jsonl"),
        "supervisor": str(root / "state" / "supervisor.jsonl"),
        "metrics": str(root / "state" / "metrics.jsonl"),
        "infra": str(root / "state" / "infra.jsonl"),
        "sessions": str(root / "state" / "sessions"),
        "cascade_answers": str(root / "logs" / "cascade-answer-*.json"),
        "ollama_sandbox": str(root / "logs" / "ollama-sandbox.log"),
        "opencode_log": str(home / ".local" / "share" / "opencode" / "log" / "opencode.log"),
    }
    return {
        "schema": "invr.access/1",
        "granted": True,
        "owner": "владелец фабрики дал полный доступ к логам",
        "paths": log_paths,
        "note": "отвечать «нет доступа к логам» запрещено: пути см. выше; "
                "смотреть через factory logs или /api/logs.",
    }
