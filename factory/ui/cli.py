"""CLI фабрики Lab-100."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..dispatch import Dispatcher
from ..domains import DomainRegistry
from ..ledger import Ledger
from ..paths import repo_root
from ..settings import load as load_settings
from ..task import Task


def build_context():
    root = repo_root()
    settings = load_settings(root / "config" / "settings.toml")
    domains_file = root / str(settings.get("domains_file", "config/domains.toml"))
    profiles_dir = root / str(settings.get("profiles_dir", "config/profiles"))
    active = str(settings.get("active_profile", "default"))
    overlays = []
    profile = profiles_dir / f"{active}.toml"
    if profile.exists():
        overlays.append(profile)
    runtime = profiles_dir / "_runtime.toml"
    if runtime.exists():
        overlays.append(runtime)
    registry = DomainRegistry(domains_file, overlays)
    ledger = Ledger(root / str(settings.get("ledger_path", "state/ledger.jsonl")))
    return root, settings, registry, ledger


def _cmd_status(args) -> int:
    root, settings, registry, ledger = build_context()
    enabled = registry.enabled()
    print(f"Корень фабрики : {root}")
    print(f"Профиль        : {settings.get('active_profile', 'default')}")
    print(f"Автоматизация  : {settings.automation} (checkpoints: {settings.checkpoints or 'все шаги'})")
    print(f"Интерфейс      : {settings.ui}")
    print(f"Домены         : {len(enabled)} включено из {len(registry.all())}")
    for dom in registry.all():
        mark = "[+]" if dom.enabled else "[-]"
        print(f"  {mark} {dom.name:<12} p{dom.priority:<3} {dom.title}")
    recent = ledger.tail(5)
    print(f"Журнал         : {ledger.path} ({len(recent)} последних событий)")
    for event in recent:
        print(f"  · {event.get('kind')} {event.get('task', '')} {event.get('status', '')}")
    return 0


def _cmd_domains(args) -> int:
    _, _, registry, _ = build_context()
    if args.action in ("list", None):
        for dom in registry.all():
            mark = "[+]" if dom.enabled else "[-]"
            caps = ", ".join(dom.capabilities)
            print(f"{mark} {dom.name:<12} p{dom.priority:<3} [{caps}]  {dom.title}")
    elif args.action == "show":
        dom = registry.get(args.name)
        if not dom:
            print(f"домен не найден: {args.name}", file=sys.stderr)
            return 1
        print(json.dumps(dom.to_dict(), ensure_ascii=False, indent=2))
    elif args.action in ("enable", "disable"):
        dom = registry.set_enabled(args.name, args.action == "enable")
        print(f"{dom.name}: enabled={dom.enabled}")
    elif args.action == "priority":
        dom = registry.set_priority(args.name, args.value)
        print(f"{dom.name}: priority={dom.priority}")
    else:
        print(f"неизвестное действие: {args.action}", file=sys.stderr)
        return 2
    return 0


def _cmd_settings(args) -> int:
    _, settings, _, _ = build_context()
    if args.action == "get":
        value = settings.get(args.key)
        print(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
    elif args.action == "set":
        value = settings.set(args.key, args.value)
        settings.save()
        print(f"{args.key} = {value!r}")
    elif args.action == "list":
        print(json.dumps(settings.data, ensure_ascii=False, indent=2))
    else:
        print(f"неизвестное действие: {args.action}", file=sys.stderr)
        return 2
    return 0


def _cmd_run(args) -> int:
    root, settings, registry, ledger = build_context()
    task = Task.load(args.file)
    if args.auto:
        task.automation = "auto"
    elif args.semi:
        task.automation = "semi"
    dispatcher = Dispatcher(settings, registry, ledger)

    def approve(step) -> bool:
        if args.yes:
            return True
        try:
            answer = input(f"Подтвердить шаг #{step.id} {step.capability} ({step.adapter}:{step.action})? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes", "д", "да")

    result = dispatcher.execute(task, approve=approve, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] or args.dry_run else 1


def _cmd_ui(args) -> int:
    mode = args.mode
    if mode == "web":
        from .web import serve
        return serve()
    if mode == "tray":
        from .tray import serve
        return serve()
    print("Интерфейс CLI: используйте `factory status`, `factory domains list` и т.д.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description="Фабрика Lab-100")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="общий статус фабрики")
    p_status.set_defaults(func=_cmd_status)

    p_domains = sub.add_parser("domains", help="подвижные домены")
    p_domains.add_argument("action", nargs="?", default="list",
                           choices=["list", "show", "enable", "disable", "priority"])
    p_domains.add_argument("name", nargs="?", default="")
    p_domains.add_argument("value", nargs="?", type=int, default=50)
    p_domains.set_defaults(func=_cmd_domains)

    p_settings = sub.add_parser("settings", help="настройки фабрики")
    p_settings.add_argument("action", nargs="?", default="list", choices=["list", "get", "set"])
    p_settings.add_argument("key", nargs="?", default="")
    p_settings.add_argument("value", nargs="?", default="")
    p_settings.set_defaults(func=_cmd_settings)

    p_run = sub.add_parser("run", help="выполнить заказ-наряд")
    p_run.add_argument("file", help="файл задачи (.json/.toml)")
    p_run.add_argument("--dry-run", action="store_true", help="без внешних вызовов")
    p_run.add_argument("--auto", action="store_true", help="полная автоматика")
    p_run.add_argument("--semi", action="store_true", help="полуавтомат с подтверждениями")
    p_run.add_argument("--yes", action="store_true", help="авто-подтверждение в режиме semi")
    p_run.set_defaults(func=_cmd_run)

    p_ui = sub.add_parser("ui", help="запустить интерфейс")
    p_ui.add_argument("--mode", choices=["cli", "web", "tray"], default="cli")
    p_ui.set_defaults(func=_cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)
