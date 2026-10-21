"""Арсенал фабрики — единый каталог всех управляемых элементов.

Фабрика не поглощает исходные репозитории: она ведёт их как «арсенал».
Этот модуль собирает в один список элементы пяти классов:

  * ``repo``    — исходные репозитории Lab-100 (обёртка над :mod:`inventory`);
  * ``tool``    — инструменты склада ``scripts-tools`` (реестр + шимы);
  * ``agent``   — исполнители Lab100 (провайдеры из ``config/agents.toml``);
  * ``service`` — службы (Ollama, browsertool, Firecrawl, MCP, OpenCode, …);
  * ``env``     — окружение OpenCode (``AGENTS.md``, ``opencode.json``,
                  ``plugin/``, ``skills/``, ``agent/``, ``rotation/``).

Модуль только читает: отсутствующий элемент попадает в каталог с
``available = false``. Проект рассчитан на работу **без Docker**; службы с
``docker = true`` помечаются как исключённые и не проверяются.

Каждый элемент несёт поле ``probe`` — *как* проверять его работоспособность
(исполняется в :mod:`factory.nodes`). Так каталог (арсенал) и проверка
состояний (узлы) разделены: арсенал отвечает на вопрос «что у нас есть»,
узлы — «что из этого работает».
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import inventory
from .paths import tools_root

SCHEMA = "invr.arsenal/1"

# Классы элементов и их владелец по умолчанию.
CLASSES = ("repo", "tool", "agent", "service", "env")
FACTORY_OWNED = ("repo", "tool", "agent", "env")

# Инструменты склада, относящиеся к Docker-стеку. Проект рассчитан на работу
# без Docker, поэтому такие инструменты помечаются как исключённые (``unloaded``),
# а не как рабочие — по аналогии со службами с ``docker = true``.
DOCKER_TOOLS = {"dmr-model", "dmr-router", "gordon", "gordon-chat"}

# Службы по умолчанию (без Docker). Настройки могут переопределить любую
# или добавить свою: [services.<name>]. Службы с docker = true исключены.
DEFAULT_SERVICES: dict[str, dict] = {
    "ollama": {
        "title": "Ollama (локальные модели)",
        "kind": "http",
        "url": "http://127.0.0.1:11434/api/tags",
        "owner": "factory",
        # Локальные модели по умолчанию ОТКЛЮЧЕНЫ. Включить: настройка
        # `[services.ollama] enabled = true` в settings.toml.
        "enabled": False,
        "start": ["ollama", "serve"],
        "stop": ["taskkill", "/IM", "ollama.exe", "/F"],
        "tags": ["llm"],
        "docker": False,
    },
    "browsertool": {
        "title": "browsertool (локальный браузер)",
        "kind": "tcp",
        "host": "127.0.0.1",
        "port": 8123,
        "owner": "factory",
        "enabled": True,
        "tags": ["browser", "scrape"],
        "docker": False,
    },
    "firecrawl": {
        "title": "Firecrawl MCP",
        "kind": "command",
        "command": ["npx", "-y", "firecrawl-mcp"],
        "owner": "factory",
        "enabled": True,
        "tags": ["mcp", "scrape"],
        "docker": False,
    },
    "webchat": {
        # Браузерные веб-чаты (DeepSeek, GigaChat, Qwen, Яндекс/Алиса):
        # локальный HTTP-демон browser-chats. Адаптер web_chat фабрики
        # обращается к нему по POST /api/ask. Супервизор поднимает и держит
        # службу, как Ollama/browsertool. Путь к скрипту и дополнительные
        # аргументы переопределяются настройкой [services.webchat] start
        # в settings.toml (здесь — переносимый дефолт для исходников).
        "title": "webchat (браузерные чаты)",
        "kind": "http",
        "url": "http://127.0.0.1:8120/api/health",
        "owner": "factory",
        "enabled": True,
        # Переносится за счёт resolve_python() и каталога рядом с репозиторием;
        # реальный путь к webchat_service.py задаётся в config/settings.toml.
        "start": [],
        "stop": [],
        "tags": ["chat", "browser"],
        "docker": False,
    },
    "opencode": {
        "title": "OpenCode",
        "kind": "process",
        "match": "opencode",
        "owner": "opencode",
        "enabled": True,
        "tags": ["ide"],
        "docker": False,
    },
    "mcp-watchdog": {
        "title": "mcp-watchdog (сторож окружения)",
        "kind": "file",
        "path": "",
        "owner": "opencode",
        "enabled": True,
        "tags": ["supervision"],
        "docker": False,
    },
    # Службы ИИ-контура: их поднимает и держит супервизор фабрики.
    # Команды запуска задаются в config/settings.toml ([services.*]) — там
    # переносимые пути через %USERPROFILE%, без персональных абсолютных путей.
    "multitool": {
        "title": "MultiTool (агрегатор бесплатных моделей)",
        "kind": "http",
        "url": "http://127.0.0.1:8799/health",
        "owner": "factory",
        "enabled": True,
        "start": [],
        "stop": [],
        "tags": ["llm", "mcp"],
        "docker": False,
    },
    "litellm": {
        "title": "AI Gateway (шлюз пулов моделей LiteLLM)",
        "kind": "http",
        "url": "http://127.0.0.1:4000/health/liveliness",
        "owner": "factory",
        "enabled": True,
        "start": [],
        "stop": [],
        "tags": ["llm", "gateway"],
        "docker": False,
    },
    "tor": {
        "title": "Tor (SOCKS 9050 для onion-MCP)",
        "kind": "tcp",
        "host": "127.0.0.1",
        "port": 9050,
        "owner": "factory",
        "enabled": True,
        "start": [],
        "stop": [],
        "tags": ["tor", "onion"],
        "docker": False,
    },
    # Исключено — проект без Docker. Показываются как «unloaded», не как сбой.
    "docker": {
        "title": "Docker Desktop (исключён)",
        "kind": "process",
        "match": "dockerd",
        "owner": "external",
        "enabled": False,
        "tags": ["docker"],
        "docker": True,
    },
    "gordon": {
        "title": "Gordon (Docker AI, исключён)",
        "kind": "http",
        "url": "http://127.0.0.1:8786",
        "owner": "external",
        "enabled": False,
        "tags": ["docker", "ai"],
        "docker": True,
    },
    "model-runner": {
        "title": "Docker Model Runner (исключён)",
        "kind": "command",
        "command": ["docker", "model"],
        "owner": "external",
        "enabled": False,
        "tags": ["docker", "llm"],
        "docker": True,
    },
}


def _base(settings) -> Path:
    """Корень фабрики (рядом с settings.toml) — устойчиво к тестам и сборке."""
    if settings is not None:
        return Path(settings.path).parent.parent
    return Path.cwd()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _shim(repo: Path, name: str) -> Path | None:
    """Найти шим инструмента (``<name>.ps1|cmd|bat|exe``) в корне склада."""
    for suffix in (".ps1", ".cmd", ".bat", ".exe"):
        candidate = repo / f"{name}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _element(*, eid: str, name: str, klass: str, source: str, title: str,
             available: bool, path: str | None = None, probe: dict | None = None,
             owner: str = "factory", enabled: bool = True,
             tags: list | None = None, meta: dict | None = None) -> dict:
    return {
        "id": eid,
        "name": name,
        "class": klass,
        "source": source,
        "title": title,
        "available": bool(available),
        "enabled": bool(enabled),
        "owner": owner,
        "path": path,
        "probe": probe,
        "tags": list(tags or []),
        "meta": dict(meta or {}),
    }


# --- классы элементов ---

def _repo_elements(inv: dict) -> list[dict]:
    out: list[dict] = []
    for name, source in (inv.get("sources") or {}).items():
        available = bool(source.get("available"))
        out.append(_element(
            eid=f"repo:{name}", name=name, klass="repo", source=name,
            title=f"Репозиторий {name}", available=available,
            path=source.get("root"),
            probe={"kind": "exists", "path": source.get("root")} if available else None,
            owner="factory",
            meta={"role": (inv.get("roles") or {}).get(name, ""),
                  "hint": source.get("hint", "")},
        ))
    return out


def _tool_elements(inv: dict) -> list[dict]:
    out: list[dict] = []
    store = (inv.get("sources") or {}).get("scripts-tools") or {}
    if not store.get("available"):
        return out
    root = Path(store.get("root", ""))
    for tool in store.get("tools") or []:
        name = tool.get("name", "")
        if not name:
            continue
        is_docker = name in DOCKER_TOOLS
        shim = _shim(root, name)
        target = shim or (root / "registry" / name)
        out.append(_element(
            eid=f"tool:{name}", name=name, klass="tool", source="scripts-tools",
            title=f"Инструмент {name}", available=bool(name),
            path=str(shim) if shim else None,
            probe={"kind": "exists", "path": str(target)},
            owner="factory", enabled=not is_docker,
            tags=["tool"] + (["docker"] if is_docker else []),
            meta={"versions": tool.get("versions", []), "latest": tool.get("latest", ""),
                  "shim": str(shim) if shim else "", "docker": is_docker,
                  "reason": "исключено: проект без Docker" if is_docker else ""},
        ))
    return out


def _agent_elements(inv: dict) -> list[dict]:
    out: list[dict] = []
    lab = (inv.get("sources") or {}).get("Lab100") or {}
    if not lab.get("available"):
        return out
    for provider in lab.get("providers") or []:
        name = provider.get("name", "")
        if not name:
            continue
        out.append(_element(
            eid=f"agent:{name}", name=name, klass="agent", source="Lab100",
            title=f"Исполнитель {name}", available=True,
            path=lab.get("root"),
            probe={"kind": "exists", "path": lab.get("root")},
            owner="factory",
            tags=["agent"],
            meta={"kind": provider.get("kind"), "capabilities": provider.get("capabilities", []),
                  "priority": provider.get("priority"), "model": provider.get("model")},
        ))
    return out


def _resolve_services(settings) -> dict[str, dict]:
    """Слить службы по умолчанию с настройками ``[services.*]``."""
    merged: dict[str, dict] = {name: dict(spec) for name, spec in DEFAULT_SERVICES.items()}
    configured = (settings.get("services") if settings is not None else None) or {}
    for name, spec in configured.items():
        if not isinstance(spec, dict):
            continue
        merged.setdefault(name, {})
        merged[name].update(spec)
    # Ollama: уважаем уже заданный host адаптера, если служба не переопределена
    ollama = merged.get("ollama", {})
    if ollama.get("kind") == "http" and not any(
            k in (configured.get("ollama") or {}) for k in ("url",)):
        host = str((settings.get("adapters.ollama_host") if settings is not None else "") or "")
        if host:
            ollama["url"] = host.rstrip("/") + "/api/tags"
    # mcp-watchdog: путь состояния из [sources], если не задан явно
    watchdog = merged.get("mcp-watchdog", {})
    if watchdog.get("kind") == "file" and not watchdog.get("path"):
        configured = str((settings.get("sources.mcp_watchdog_state")
                          if settings is not None else "") or "").strip()
        if not configured:
            # переносимо: выводим из корня склада инструментов (INVR_TOOLS_ROOT)
            store = tools_root()
            if store is not None:
                configured = str(store / "mcp-watchdog" / "state" / "current.json")
        watchdog["path"] = configured
    return merged


def _service_elements(settings) -> list[dict]:
    out: list[dict] = []
    for name, spec in _resolve_services(settings).items():
        docker = bool(spec.get("docker"))
        enabled = bool(spec.get("enabled", True)) and not docker
        kind = str(spec.get("kind") or "command")
        probe = None
        if not docker:
            probe = {"kind": kind}
            for key in ("url", "host", "port", "command", "path", "match"):
                if spec.get(key) not in (None, "", []):
                    probe[key] = spec[key]
        out.append(_element(
            eid=f"service:{name}", name=name, klass="service", source="services",
            title=str(spec.get("title") or name), available=enabled,
            path=spec.get("path") or spec.get("url"),
            probe=probe, owner=str(spec.get("owner") or "external"), enabled=enabled,
            tags=list(spec.get("tags") or []),
            meta={"kind": kind, "docker": docker, "start": spec.get("start") or [],
                  "stop": spec.get("stop") or [], "log": spec.get("log") or "",
                  "reason": "исключено: проект без Docker" if docker else ""},
        ))
    return out


def _env_elements(settings) -> list[dict]:
    oc = (settings.get("opencode") if settings is not None else None) or {}
    root = Path(oc.get("config_dir") or (Path.home() / ".config" / "opencode"))
    required = list(oc.get("required") or ["AGENTS.md", "opencode.json"])
    optional = list(oc.get("optional") or ["plugin", "skills", "agent", "rotation"])
    out: list[dict] = []
    for name in required + optional:
        path = root / name
        out.append(_element(
            eid=f"env:{name}", name=name, klass="env", source="opencode",
            title=f"OpenCode: {name}", available=path.exists(), path=str(path),
            probe={"kind": "exists", "path": str(path)}, owner="factory",
            tags=["opencode", "env"],
            meta={"required": name in required, "root": str(root)},
        ))
    return out


def build(settings=None) -> dict:
    """Собрать каталог арсенала: все управляемые элементы и их состояние наличия."""
    inv = inventory.build(settings)
    elements: list[dict] = []
    elements += _repo_elements(inv)
    elements += _tool_elements(inv)
    elements += _agent_elements(inv)
    elements += _service_elements(settings)
    elements += _env_elements(settings)

    by_class: dict[str, int] = {}
    present = 0
    for element in elements:
        by_class[element["class"]] = by_class.get(element["class"], 0) + 1
        if element["available"]:
            present += 1
    summary = {
        "total": len(elements),
        "present": present,
        "by_class": by_class,
        "repos_present": inv.get("summary", {}).get("repos_present", 0),
        "repos_total": inv.get("summary", {}).get("repos_total", 0),
        "docker_excluded": sum(1 for e in elements if (e.get("meta") or {}).get("docker")),
    }
    return {
        "schema": SCHEMA,
        "generated": _now(),
        "base": str(_base(settings)),
        "elements": elements,
        "summary": summary,
    }


def by_id(data: dict) -> dict[str, dict]:
    """Индекс элементов по id."""
    return {element["id"]: element for element in data.get("elements", [])}


def save(settings, data: dict | None = None) -> Path:
    """Сохранить каталог в ``state/arsenal.json`` (путь — ``arsenal.path``)."""
    data = data or build(settings)
    path = _base(settings) / str(settings.get("arsenal.path", "state/arsenal.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_saved(settings) -> dict | None:
    path = _base(settings) / str(settings.get("arsenal.path", "state/arsenal.json"))
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(text) if text.strip() else None
    except (OSError, json.JSONDecodeError):
        return None
