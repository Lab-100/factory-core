"""Выборка возможностей всех провайдеров фабрики (MCP/скиллы/плагины/субмодели/серверы).

Собирает фактические данные с диска и из конфигов:

  * mcp      — серверы из `opencode.json` (имя, команда, что делает, когда запускается);
  * skills   — каталог скиллов `~/.config/opencode/skills` (что делает, когда запускается);
  * plugins  — каталог плагинов `~/.config/opencode/plugins`;
  * submodels— модели провайдеров (Cline models, OpenCode free, Ollama, Lab100 LLM);
  * servers  — узлы `infra-graph`/`nodes.json` (класс, состояние, интент, владелец).

Данные только читаются; внешние процессы не запускаются. Используется CLI
(`factory capabilities`) и дашборд (`/api/capabilities`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import providers, infragraph
from .inventory import lab100_config


def _load_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_dir(path: Path) -> list[Path]:
    try:
        if not path.is_dir():
            return []
        return sorted([p for p in path.iterdir()], key=lambda p: p.name.lower())
    except OSError:
        return []


def _opencode_config(settings) -> Path:
    cfg = str(settings.get("opencode.config_dir", "") or "").strip() if settings is not None else ""
    return Path(cfg) if cfg else Path.home() / ".config" / "opencode"


def _mcp(settings) -> list[dict]:
    cfg = _load_json(_opencode_config(settings) / "opencode.json")
    mcp = cfg.get("mcp") or {}
    out: list[dict] = []
    for name, spec in mcp.items():
        if not isinstance(spec, dict):
            continue
        cmd = spec.get("command") or []
        out.append({
            "id": name,
            "type": spec.get("type", "local"),
            "command": list(cmd) if isinstance(cmd, list) else [str(cmd)],
            "enabled": bool(spec.get("enabled", True)),
            "timeout": spec.get("timeout"),
            "when": "автозапуск при старте opencode" if spec.get("enabled", True) else "отключён",
        })
    return out


def _skills(settings) -> list[dict]:
    base = _opencode_config(settings) / "skills"
    out: list[dict] = []
    for entry in _read_dir(base):
        if not entry.is_dir():
            continue
        name = entry.name
        desc = ""
        sk = entry / "SKILL.md"
        if sk.is_file():
            try:
                head = sk.read_text(encoding="utf-8", errors="replace")[:400]
                for line in head.splitlines():
                    line = line.strip()
                    if line.lower().startswith("description") and ":" in line:
                        desc = line.split(":", 1)[1].strip().strip('"').strip()
                        break
                if not desc:
                    desc = head.splitlines()[0] if head.splitlines() else ""
            except OSError:
                pass
        out.append({"id": name, "what": desc or "описание в SKILL.md",
                    "when": "по требованию (загружается в контекст при релевантной задаче)"})
    return out


def _plugins(settings) -> list[dict]:
    base = _opencode_config(settings) / "plugins"
    out: list[dict] = []
    for entry in _read_dir(base):
        if entry.is_file():
            out.append({"id": entry.name,
                        "what": "плагин окружения opencode",
                        "when": "при старте сессии"})
    return out


def _submodels(settings) -> list[dict]:
    out: list[dict] = []
    # Cline
    cline_models = []
    if settings is not None:
        raw = settings.get("providers.cline.models") or []
        if isinstance(raw, (list, tuple)):
            cline_models = [str(m) for m in raw if str(m).strip()]
    out.append({"provider": "cline", "kind": "models",
                "count": len(cline_models),
                "models": cline_models,
                "what": "перебор моделей Cline CLI (first-working)",
                "when": "при запросе через Cline (провайдер включён)"})
    # OpenCode free
    free = ["opencode/space-bunny-free", "opencode/mimo-v2.6-flash-free",
            "opencode/longcat-2.5-preview-free"]
    out.append({"provider": "opencode", "kind": "models",
                "count": len(free), "models": free,
                "what": "бесплатные модели OpenCode",
                "when": "при запросе через opencode run --model"})
    # Ollama локальные
    ollama_models = []
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ollama_models = [str(m.get("name")) for m in data.get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        pass
    out.append({"provider": "ollama", "kind": "models",
                "count": len(ollama_models), "models": ollama_models,
                "what": "локальные модели Ollama",
                "when": "при запросе через llm_local (если включён)"})
    # Lab100 LLM
    _, lab_cfg = lab100_config(settings)
    llm = []
    for name, sec in (lab_cfg.get("provider") or {}).items():
        if sec.get("kind") == "llm":
            llm.append({"name": name, "model": sec.get("model"), "enabled": sec.get("enabled", True)})
    out.append({"provider": "Lab100", "kind": "llm-providers",
                "count": len(llm), "providers": llm,
                "what": "LLM-провайдеры пула Lab100",
                "when": "при использовании адаптера lab100"})
    return out


def _servers(settings) -> list[dict]:
    data = infragraph.load(settings)
    nodes = data.get("nodes") or []
    out: list[dict] = []
    for n in nodes:
        out.append({
            "id": n.get("id"),
            "class": n.get("class"),
            "state": n.get("state"),
            "points_percent": n.get("points_percent"),
            "intent": n.get("intent"),
            "owner": n.get("owner"),
            "when": ("работает" if n.get("state") == "active"
                     else ("простаивает" if n.get("state") == "idle" else "не запущен")),
        })
    return out


def build(settings=None) -> dict:
    return {
        "schema": "invr.capabilities/1",
        "generated": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "mcp": _mcp(settings),
        "skills": _skills(settings),
        "plugins": _plugins(settings),
        "submodels": _submodels(settings),
        "servers": _servers(settings),
        "summary": {
            "mcp": len(_mcp(settings)),
            "skills": len(_skills(settings)),
            "plugins": len(_plugins(settings)),
            "submodels": len(_submodels(settings)),
            "servers": len(_servers(settings)),
        },
    }


def render_text(data: dict) -> str:
    lines = ["Выборка возможностей провайдеров фабрики", ""]
    lines.append(f"MCP-серверы ({len(data['mcp'])}):")
    for m in data["mcp"]:
        lines.append(f"  - {m['id']}: {', '.join(m['command']) or '—'} | {m['when']}")
    lines.append(f"Скиллы ({len(data['skills'])}):")
    for s in data["skills"]:
        lines.append(f"  - {s['id']}: {s['what']} | {s['when']}")
    lines.append(f"Плагины ({len(data['plugins'])}):")
    for p in data["plugins"]:
        lines.append(f"  - {p['id']}: {p['what']} | {p['when']}")
    lines.append(f"Субмодели ({len(data['submodels'])} групп):")
    for sm in data["submodels"]:
        lines.append(f"  - {sm['provider']} ({sm['count']}): {sm['what']} | {sm['when']}")
    lines.append(f"Серверы ({len(data['servers'])}):")
    for s in data["servers"]:
        lines.append(f"  - {s['id']} [{s['class']}] {s['state']} | {s['when']}")
    return "\n".join(lines)