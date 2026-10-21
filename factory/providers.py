"""Внешние ИИ-провайдеры фабрики — переключатель бесплатных встроенных агентов.

Фабрика автономна (Lab100 / INVR / локальная LLM), но умеет и подключаться к
уже установленным средам разработки — OpenCode, Claude Code, Cursor, Windsurf,
Void, Zen, workAI, MultiTool, Cline: их встроенные бесплатные ассистенты можно
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
from pathlib import Path

from .paths import resolve_python

# Каталог известных сред. Для каждой — вероятные имена CLI и каталоги конфигов.
PROVIDERS: dict[str, dict] = {
    # Провайдеры ответов/компетенций — см. также адаптер external_agent.
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
    "cascade": {
        "title": "Cascade (headless-каскад: pollinations → opencode → claude → workai)",
        "commands": [],
        "paths": [],
        # headless-каскад: stdin -> stdout (без окон, локальный, бесплатный).
        "run": ["bridge"],
        "bridge_default": "cascade_bridge.py",
    },
    "multitool": {
        "title": "MultiTool",
        "commands": ["multitool", "mt"],
        "paths": ["~/.multitool"],
        "run": ["multitool", "run"],
    },
    "unitool": {
        "title": "Unitool (единый инструмент: поиск + свободные модели + шлюз LiteLLM)",
        # Скрипт `unitool` ставится вместе с пакетом (pip install -e .) и должен
        # быть в PATH; иначе используется адаптер factory/adapters/unitool.py.
        "commands": ["unitool"],
        "paths": ["~/Documents/Lab-100/unitool"],
        # CLI unitool: подкоманды status|search|scrape|ask|models|doctor.
        # Промпт передаётся аргументом, ответ печатается в stdout.
        "run": ["unitool", "ask"],
        "prompt_arg": True,
    },
    "pollinations": {
        "title": "Pollinations",
        "commands": ["pollinations-bridge"],
        "paths": [],
        # промпт на stdin, ответ в stdout (OpenAI-совместимый эндпоинт).
        "run": ["bridge"],
    },
    "cline": {
        "title": "Cline",
        "commands": ["cline", "cline-cli"],
        "paths": ["~/AppData/Local/Cline", "~/.cline"],
        "run": ["cline"],
        # CLI Cline принимает промпт аргументом, а не через stdin:
        # адаптер external_agent дописывает промпт последним аргументом.
        "prompt_arg": True,
    },
    "koda": {
        "title": "Koda (Koda Code / Koda Desktop)",
        "commands": ["koda", "koda.cmd"],
        "paths": ["~/AppData/Local/Programs/koda-app", "~/AppData/Local/hermes/node"],
        # Koda принимает промпт флагом -p (неинтерактивный режим); мост
        # приводит её к общему формату stdin -> stdout, как WorkAI.
        "run": ["bridge"],
        "bridge_default": "koda_bridge.py",
    },
    "web_chat": {
        # Браузерные веб-чаты (DeepSeek, GigaChat, Qwen, Яндекс/Алиса) —
        # доступ через локальный HTTP-демон browser-chats. Адаптер web_chat
        # опрашивает его по REST; это не CLI, поэтому "pуны"/команды пусты.
        "title": "Web-чаты (браузер)",
        "commands": [],
        "paths": [],
        # Признак-маркер: демон-сервис, а не CLI-агент (см. web_chat.py).
        "service": True,
    },
}

# Путь по умолчанию к мосту external_agent -> WorkAI (reads stdin -> prints answer).
# Мост лежит рядом с пакетом: <factory-core>/factory/adapters/workai_bridge.py.
# В замороженной сборке он копируется в рабочий корень (см. paths._seed_bundled).
# Переопределяется настройкой providers.workai.bridge.
WORKAI_BRIDGE_DEFAULT = str(
    Path(__file__).resolve().parent / "adapters" / "workai_bridge.py"
)

# Путь по умолчанию к мосту external_agent -> Cline (reads stdin -> prints answer).
# По умолчанию мост лежит рядом с module: <factory-core>/factory/adapters/cline_bridge.py.
# Переопределяется настройкой providers.cline.bridge.
CLINE_BRIDGE_DEFAULT = str(
    Path(__file__).resolve().parent / "adapters" / "cline_bridge.py"
)

# Путь по умолчанию к мосту external_agent -> Koda (reads stdin -> prints answer).
# Koda CLI в неинтерактивном режиме (`koda -p`) сам печатает ответ, мост лишь
# приводит запуск к общему формату фабрики и ищет установленную Koda.
# Переопределяется настройкой providers.koda.bridge.
KODA_BRIDGE_DEFAULT = str(
    Path(__file__).resolve().parent / "adapters" / "koda_bridge.py"
)

# Путь по умолчанию к мосту headless-каскада (stdin -> stdout). Каскад пробует
# Ollama → opencode → pollinations → claude-code → workai-gui и возвращает
# первый непустой ответ. Без окон, локальный, бесплатный.
CASCADE_BRIDGE_DEFAULT = str(
    Path(__file__).resolve().parent / "adapters" / "cascade_bridge.py"
)
CASCADE_BRIDGE_SANDBOX = str(
    Path(__file__).resolve().parent / "adapters" / "ollama_sandbox.py"
)

# Бесплатная модель Cline по умолчанию (цена 0, баланс не требуется).
# Переопределяется настройкой providers.cline.model.
CLINE_MODEL_DEFAULT = "cline-free/mimo-v2.6-flash"


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
        "prompt_arg": bool(spec.get("prompt_arg")),
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
        # В замороженной сборке путь рядом с пакетом внутри архива не работает:
        # берём копию моста, развёрнутую в рабочий корень (paths._seed_bundled).
        if not os.path.isfile(str(bridge)):
            from .paths import repo_root
            seeded = repo_root(settings) if settings is not None else repo_root()
            candidate = str(seeded / "workai_bridge.py")
            if os.path.isfile(candidate):
                bridge = candidate
        python = resolve_python()
        if settings is not None:
            configured_python = settings.get("adapters.workai_python")
            if configured_python:
                python = str(configured_python)
        return [python, bridge]

    if str(name) == "koda":
        # Мост external_agent -> Koda: stdin -> stdout. В замороженной сборке
        # берём копию, развёрнутую в рабочий корень рядом с exe.
        bridge = KODA_BRIDGE_DEFAULT
        if settings is not None:
            configured = str(settings.get("providers.koda.bridge", "") or "").strip()
            if configured:
                bridge = configured
        if not os.path.isfile(str(bridge)):
            from .paths import repo_root
            seeded = repo_root(settings) if settings is not None else repo_root()
            candidate = str(seeded / "koda_bridge.py")
            if os.path.isfile(candidate):
                bridge = candidate
        python = resolve_python()
        return [python, bridge]

    if str(name) == "cascade":
        # Headless-каскад: reads stdin -> produces answer. Мост лежит рядом с
        # пакетом; переопределяется providers.cascade.bridge.
        bridge = CASCADE_BRIDGE_DEFAULT
        if settings is not None:
            configured = str(settings.get("providers.cascade.bridge", "") or "").strip()
            if configured:
                bridge = configured
        if not os.path.isfile(str(bridge)):
            from .paths import repo_root
            seeded = repo_root(settings) if settings is not None else repo_root()
            candidate = str(seeded / "cascade_bridge.py")
            if os.path.isfile(candidate):
                bridge = candidate
        python = resolve_python()
        return [python, bridge]

    if str(name) == "cline":
        # CLI Cline принимает промпт аргументом (не через stdin), поэтому
        # адаптер external_agent допишет промпт последним аргументом
        # (spec["prompt_arg"]). Мост не нужен, и это работает в frozen-сборке.
        configured = _configured_command(settings, name)
        if configured:
            command = _which(configured) or configured
        else:
            info = provider_info(settings, name)
            command = info.get("command") or ""
        if not command:
            return []
        model = ""
        if settings is not None:
            model = str(settings.get("providers.cline.model", "") or "").strip()
        if not model:
            model = CLINE_MODEL_DEFAULT
        args = [command, "--json", "--auto-approve", "true"]
        if model:
            args.extend(["-m", model])
        return args

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


def _cline_models(settings) -> list[str]:
    """Упорядоченный список моделей Cline для перебора (providers.cline.models)."""
    models: list[str] = []
    if settings is not None:
        raw = settings.get("providers.cline.models")
        if isinstance(raw, (list, tuple)):
            models = [str(item).strip() for item in raw if str(item).strip()]
        if not models:
            single = str(settings.get("providers.cline.model", "") or "").strip()
            if single:
                models = [single]
    if not models:
        models = [CLINE_MODEL_DEFAULT]
    seen: set[str] = set()
    unique: list[str] = []
    for item in models:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def run_commands(settings, name: str) -> list[list[str]]:
    """Все варианты запуска провайдера (для cline — перебор моделей)."""
    if str(name) == "cline":
        configured = _configured_command(settings, name)
        if configured:
            command = _which(configured) or configured
        else:
            info = provider_info(settings, name)
            command = info.get("command") or ""
        if not command:
            return []
        base = [command, "--json", "--auto-approve", "true"]
        variants: list[list[str]] = []
        for entry in _cline_models(settings):
            provider_id = ""
            model = entry
            if "|" in entry:
                provider_id, model = entry.split("|", 1)
                provider_id = provider_id.strip()
                model = model.strip()
            if not model:
                continue
            args = list(base)
            if provider_id:
                args.extend(["-P", provider_id])
            args.extend(["-m", model])
            variants.append(args)
        return variants
    single = run_command(settings, name)
    return [single] if single else []


def extract_cline_text(raw: str) -> str:
    """Финальный текст ответа Cline из JSON-строк вывода (run_result.text)."""
    import json

    result_text = ""
    done_text = ""
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        kind = str(item.get("type") or "")
        if kind == "run_result":
            text = item.get("text")
            if text:
                result_text = str(text)
        elif kind == "agent_event":
            event = item.get("event") or {}
            if event.get("type") == "done":
                text = event.get("text")
                if text:
                    done_text = str(text)
    return (result_text or done_text).strip()


def set_enabled(settings, name: str, value: bool) -> bool:
    """Переключатель провайдера; сохраняет настройки. False — провайдер неизвестен."""
    if str(name) not in PROVIDERS:
        return False
    settings.set(f"providers.{name}.enabled", bool(value))
    settings.save()
    return True
