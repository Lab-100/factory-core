"""Инвентаризация фабрики — этап 0 дорожной карты: карта «что умеем».

Собирает каталог инструментов, ИИ-провайдеров, регионов и этапов развёртывания
из исходных репозиториев Lab-100:

  * scripts-tools     — склад версионированных инструментов (registry/ + шимы);
  * Lab100            — пул ИИ-агентов (config/agents.toml: providers/quota);
  * region-spoof      — регионы и стратегии (config.json);
  * devstation-deploy — этапы монтажа/наладки.

Модуль только читает; исходные репозитории не изменяются. Отсутствующий
репозиторий не считается ошибкой — он попадает в карту с `available = false`.
"""
from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .paths import repo_root, tools_root

SHIM_SUFFIXES = (".ps1", ".cmd", ".bat", ".exe")

# Описание источников: где искать репозиторий и по каким признакам опознавать.
REPOS: dict[str, dict] = {
    "scripts-tools": {
        "setting": "adapters.invr_root",
        "env": ("INVR_TOOLS_ROOT", "DEVSTATION_TOOLS"),
        "names": ("scripts-tools", "tools"),
        "probe": ("registry", "resolve-tools.ps1", "resolve-tools.cmd"),
    },
    "Lab100": {
        "setting": "adapters.lab100_dir",
        "env": ("LAB100_DIR",),
        "names": ("Lab100", "lab100"),
        "probe": ("config/agents.toml", "lab100/cli.py"),
    },
    "region-spoof": {
        "setting": "adapters.region_repo",
        "env": ("REGION_SPOOF_DIR", "REGION_SPOOF_ROOT"),
        "names": ("region-spoof",),
        "probe": ("config.json",),
    },
    "devstation-deploy": {
        "setting": "adapters.deploy_dir",
        "env": ("DEVSTATION_DEPLOY_DIR", "DEPLOY_DIR"),
        "names": ("devstation-deploy",),
        "probe": ("project.json", "deploy.toml", "deploy.json", "scripts"),
    },
}


def _base(settings) -> Path:
    """Корень фабрики: рядом с settings.toml (устойчиво к тестам/сборке)."""
    if settings is not None:
        return Path(settings.path).parent.parent
    return repo_root()


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text) if text.strip() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _locate(settings, spec: dict) -> Path | None:
    candidates: list[Path] = []
    configured = str(settings.get(spec["setting"]) or "") if settings is not None else ""
    if configured:
        candidates.append(Path(configured))
    for env_name in spec["env"]:
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    base = _base(settings)
    for name in spec["names"]:
        candidates += [base.parent / name, base / name, Path.cwd() / name]
    # Общий корень склада инструментов (env INVR_TOOLS_ROOT или авто-поиск):
    # так склад находится так же, как его видит адаптер invr.
    store = tools_root()
    if store is not None:
        candidates.append(store)
    for candidate in candidates:
        if candidate and any((candidate / probe).exists() for probe in spec["probe"]):
            return candidate
    return None


def locate(settings, name: str) -> Path | None:
    """Найти каталог исходного репозитория по имени источника."""
    spec = REPOS.get(name)
    return _locate(settings, spec) if spec else None


def hint_for(name: str) -> str:
    """Подсказка, как подключить отсутствующий источник (настройка/переменная/путь)."""
    spec = REPOS.get(name)
    if not spec:
        return ""
    env = "/".join(spec["env"])
    names = " или ".join(spec["names"])
    return (f"задайте «{spec['setting']}» либо переменную окружения {env}, "
            f"или положите каталог {names} рядом с фабрикой")


def lab100_config(settings) -> tuple[Path | None, dict]:
    """Каталог Lab100 и разобранный config/agents.toml (для инвентаря и квот)."""
    repo = locate(settings, "Lab100")
    if repo is None:
        return None, {}
    return repo, _load_toml(repo / "config" / "agents.toml")


# --- сканеры источников ---
def _scan_tools(repo: Path) -> dict:
    registry = repo / "registry"
    tools = []
    if registry.is_dir():
        for entry in sorted(registry.iterdir()):
            if entry.is_dir() and not entry.name.startswith("_"):
                versions = sorted(p.name for p in entry.iterdir() if p.is_dir())
                latest = entry / "latest.txt"
                tools.append({
                    "name": entry.name,
                    "versions": versions,
                    "latest": latest.read_text(encoding="utf-8").strip()
                    if latest.exists() else (versions[-1] if versions else ""),
                })
    index = _load_json(registry / "_registry.json")
    shims = sorted({p.stem for p in repo.iterdir() if p.suffix.lower() in SHIM_SUFFIXES})
    return {"available": True, "root": str(repo), "count": len(tools),
            "tools": tools, "shims": shims, "registry_index": index}


def _scan_lab100(repo: Path) -> dict:
    config = _load_toml(repo / "config" / "agents.toml")
    providers = []
    for name, section in (config.get("provider") or {}).items():
        providers.append({
            "name": name,
            "kind": section.get("kind"),
            "capabilities": list(section.get("capabilities", []) or []),
            "priority": section.get("priority"),
            "model": section.get("model"),
        })
    quotas = {
        name: {"window": section.get("window"), "limit": section.get("limit")}
        for name, section in (config.get("quota") or {}).items()
    }
    return {"available": True, "root": str(repo), "count": len(providers),
            "providers": providers, "quotas": quotas, "pool": config.get("pool") or {}}


def _scan_region(repo: Path) -> dict:
    config = _load_json(repo / "config.json")
    regions = [r.get("code") for r in config.get("regions", []) if isinstance(r, dict)]
    return {"available": True, "root": str(repo),
            "region": config.get("region"), "zone": config.get("zone"),
            "strategies": config.get("strategies") or config.get("strategy"),
            "regions": regions}


def _scan_deploy(repo: Path) -> dict:
    stages = []
    for name in ("project.json", "deploy.json", "deploy.toml", "project.toml"):
        path = repo / name
        if not path.exists():
            continue
        data = _load_json(path) if path.suffix == ".json" else _load_toml(path)
        raw = data.get("stages") or data.get("steps") or data.get("pipeline")
        if isinstance(raw, list) and raw:
            stages = [s.get("name") if isinstance(s, dict) else str(s) for s in raw]
            break
    if not stages:
        scripts = repo / "scripts"
        if scripts.is_dir():
            stages = sorted(p.stem for p in scripts.iterdir() if p.suffix.lower() == ".ps1")
    return {"available": True, "root": str(repo), "count": len(stages), "stages": stages}


def build(settings=None) -> dict:
    """Собрать карту «что умеем» из доступных исходных репозиториев."""
    sources: dict[str, dict] = {}
    for name, spec in REPOS.items():
        repo = _locate(settings, spec)
        if repo is None:
            sources[name] = {"available": False, "hint": hint_for(name)}
            continue
        if name == "scripts-tools":
            sources[name] = _scan_tools(repo)
        elif name == "Lab100":
            sources[name] = _scan_lab100(repo)
        elif name == "region-spoof":
            sources[name] = _scan_region(repo)
        else:
            sources[name] = _scan_deploy(repo)

    capabilities: set[str] = set()
    for provider in sources.get("Lab100", {}).get("providers", []):
        capabilities.update(provider.get("capabilities") or [])

    summary = {
        "repos_total": len(REPOS),
        "repos_present": sum(1 for source in sources.values() if source.get("available")),
        "tools": sources.get("scripts-tools", {}).get("count", 0),
        "shims": len(sources.get("scripts-tools", {}).get("shims", [])),
        "providers": sources.get("Lab100", {}).get("count", 0),
        "regions": len(sources.get("region-spoof", {}).get("regions", [])),
        "stages": sources.get("devstation-deploy", {}).get("count", 0),
    }
    return {
        "schema": "invr.inventory/1",
        "generated": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "summary": summary,
        "capabilities": sorted(capabilities),
    }


def save(settings, data: dict | None = None) -> Path:
    """Сохранить карту в state/inventory.json (путь — inventory.path)."""
    data = data or build(settings)
    path = _base(settings) / str(settings.get("inventory.path", "state/inventory.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_saved(settings) -> dict | None:
    path = _base(settings) / str(settings.get("inventory.path", "state/inventory.json"))
    if not path.exists():
        return None
    return _load_json(path) or None
