"""Внешние ИИ-провайдеры фабрики — переключатель бесплатных встроенных агентов.

Фабрика автономна (Lab100 / INVR / локальная LLM), но умеет и подключаться к
уже установленным средам разработки — OpenCode, Claude Code, Cursor, Windsurf,
Void, Zen, workAI, MultiTool: их встроенные бесплатные ассистенты можно
«включить» и направить на них компетенции (`routing.answer = "external_agent"`).

Модуль только находит и переключает провайдеров: он ничего не запускает сам.
Запуск выполняет адаптер `external_agent` (factory/adapters/external_agent.py).

Каталог провайдеров: имя, человекочитаемое название, возможные команды CLI
(ищутся в PATH) и каталоги конфигурации/данных. Доступность = найдена хотя бы
одна команда или каталог; включённость — настройка `providers.<имя>.enabled`.
Внешних вызовов модуль не делает.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Каталог известных сред. Для каждой — вероятные имена CLI и каталоги конфигов.
PROVIDERS: dict[str, dict] = {
    "opencode": {
        "title": "OpenCode",
        "commands": ["opencode"],
        "paths": ["~/.config/opencode", "~/.local/share/opencode"],
        "run": ["opencode", "run"],
    },
    "claude-code": {
        "title": "Claude Code",
        "commands": ["claude"],
        "paths": ["~/.claude", "~/.config/claude"],
        "run": ["claude", "-p"],
    },
    "cursor": {
        "title": "Cursor",
        "commands": ["cursor-agent", "cursor"],
        "paths": ["~/.cursor"],
        "run": ["cursor-agent", "-p"],
    },
    "windsurf": {
        "title": "Windsurf",
        "commands": ["windsurf"],
        "paths": ["~/.windsurf", "~/.codeium/windsurf"],
        "run": ["windsurf", "run"],
    },
    "void": {
        "title": "Void",
        "commands": ["void"],
        "paths": ["~/.void"],
        "run": ["void", "run"],
    },
    "zen": {
        "title": "Zen",
        "commands": ["zen"],
        "paths": ["~/.zen"],
        "run": ["zen", "run"],
    },
    "workai": {
        "title": "workAI",
        "commands": ["workai"],
        "paths": ["~/.workai"],
        # spec-маркер: запуск через мост, который читает промпт из stdin и
        # возвращает ответ WorkAI в stdout (графический `workai chat` сам по
        # себе ответ в stdout не печатает).
        "run": ["bridge"],
    },
    "multitool": {
        "title": "MultiTool",
        "commands": ["multitool", "mt"],
        "paths": ["~/.multitool"],
        "run": ["multitool", "run"],
    },
}

# Путь по умолчанию к мосту external_agent -> WorkAI (reads stdin -> prints answer).
# Переопределяется настройкой providers.workai.bridge.
WORKAI_BRIDGE_DEFAULT = (
    r"C:\Users\TEKK\Documents\Codex\2026-09-01\new-chat\workai_bridge.py"
)


def names() -> list[str]:
    """Имена известных провайдеров (по алфавиту)."""
    return sorted(PROVIDERS)


def title(name: str) -> str:
    return str(PROVIDERS.get(str(name), {}).get("title") or name)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(str(path)))


def _which(command: str) -> str | None:
    found = shutil.which(str(command))
    return found or None


def _configured_command(settings, name: str) -> str:
    """Команда, заданная настройкой providers.<имя>.command (или пусто)."""
    if settings is None:
        return ""
    return str(settings.get(f"providers.{name}.command", "") or "").strip()


def provider_info(settings, name: str) -> dict:
    """Сведения об одном провайдере: доступность, команда, каталоги, включённость."""
    spec = PROVIDERS.get(str(name))
    if spec is None:
        return {"name": name, "title": name, "known": False, "available": False,
                "enabled": False, "command": None, "paths": []}
    command: str | None = None
    configured = _configured_command(settings, name)
    if configured:
        command = _which(configured) or (configured if _expand(configured).exists() else None)
    if command is None:
        for candidate in spec.get("commands", []):
            found = _which(candidate)
            if found:
                command = found
                break
    paths = [str(p) for p in (_expand(item) for item in spec.get("paths", [])) if p.exists()]
    enabled = bool(settings.get(f"providers.{name}.enabled", False)) if settings is not None else False
    return {
        "name": name,
        "title": spec["title"],
        "known": True,
        "command": command,
        "paths": paths,
        "available": bool(command or paths),
        "enabled": enabled,
        "run": list(spec.get("run") or []),
    }


def discover(settings=None) -> list[dict]:
    """Все известные провайдеры со сведениями (по алфавиту)."""
    return [provider_info(settings, name) for name in names()]


def enabled(settings) -> list[dict]:
    """Только включённые провайдеры (в порядке алфавита)."""
    return [item for item in discover(settings) if item["enabled"]]


def run_command(settings, name: str) -> list[str]:
    """Команда запуска провайдера: настроенная или из каталога; иначе пустой список.

    Для workai возвращается мост `external_agent -> WorkAI`: он читает промпт
    из stdin, запускает `workai chat`, опрашивает сессию чата и печатает ответ
    в stdout. Это нужно, т.к. графический `workai chat` ответ в stdout не отдаёт.
    """
    if str(name) == "workai":
        bridge = WORKAI_BRIDGE_DEFAULT
        if settings is not None:
            configured = str(settings.get("providers.workai.bridge", "") or "").strip()
            if configured:
                bridge = configured
        python = sys.executable
        if settings is not None:
            configured_python = settings.get("adapters.workai_python")
            if configured_python:
                python = str(configured_python)
        return [python, bridge]

    configured = _configured_command(settings, name)
    if configured:
        found = _which(configured) or configured
        spec = PROVIDERS.get(str(name), {})
        rest = list(spec.get("run") or [])[1:]
        return [found, *rest]
    info = provider_info(settings, name)
    if not info["available"]:
        return []
    if info["command"]:
        spec = PROVIDERS.get(str(name), {})
        rest = list(spec.get("run") or [])[1:]
        return [info["command"], *rest]
    return []


def set_enabled(settings, name: str, value: bool) -> bool:
    """Переключатель провайдера; сохраняет настройки. False — провайдер неизвестен."""
    if str(name) not in PROVIDERS:
        return False
    settings.set(f"providers.{name}.enabled", bool(value))
    settings.save()
    return True
