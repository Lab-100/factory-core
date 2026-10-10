"""CLI фабрики Lab-100."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import regionlib
from ..context import build_context
from ..dispatch import Dispatcher
from ..doctor import run_doctor, summarize
from ..task import Task


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


def _cmd_doctor(args) -> int:
    """Диагностика: какие исполнители и зависимости доступны."""
    _, settings, _, _ = build_context()
    rows = run_doctor(settings)
    for row in rows:
        mark = "[ok]" if row.get("available") else "[--]"
        print(f"{mark} {row.get('adapter')}")
        for key, value in row.items():
            if key in ("adapter", "available") or value in (None, [], {}, ""):
                continue
            print(f"      {key}: {value}")
    ready, total = summarize(rows)
    print(f"\nГотово адаптеров: {ready}/{total}")
    if ready < total:
        print("Подсказка: адаптеры ищут свои репозитории рядом с factory-core "
              "(Lab100, region-spoof) или по переменным окружения "
              "(INVR_TOOLS_ROOT, LAB100_DIR, REGION_SPOOF_DIR).")
    return 0 if ready else 1


def _cmd_region(args) -> int:
    """Управление регионом через region-spoof (протокол см. docs/REGION_PROTOCOL.md)."""
    _, settings, _, _ = build_context()
    repo = regionlib.find_repo(settings)
    if repo is None:
        print("region-spoof не найден (adapters.region_repo / REGION_SPOOF_DIR / рядом с factory-core)")
        return 1
    if args.action in ("status", None):
        print(json.dumps(regionlib.status(repo), ensure_ascii=False, indent=2))
        return 0
    if args.action == "switch":
        cli = regionlib.find_cli(repo)
        if cli:
            data = regionlib.call_cli(cli, "switch", region=args.region, zone=args.zone)
        else:
            data = regionlib.switch(repo, region=args.region, zone=args.zone)
            data["note"] = ("config.json обновлён; region-spoof применит регион при "
                            "следующем запуске. Для горячего переключения добавьте её CLI "
                            "(docs/REGION_PROTOCOL.md).")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"неизвестное действие: {args.action}", file=sys.stderr)
    return 2


def _cmd_jobs(args) -> int:
    """Список задач в очереди фабрики (state/jobs.jsonl)."""
    root, _, _, _ = build_context()
    from ..jobs import JobManager

    manager = JobManager(root)
    jobs = manager.list()
    if not jobs:
        print("Очередь пуста.")
        return 0
    for job in jobs:
        status = job.get("status")
        goal = str(job.get("goal", ""))[:60]
        print(f"[{status:<18}] {job.get('id')}  {goal}")
        if job.get("error"):
            print(f"      error: {job['error']}")
    return 0


def _cmd_auth(args) -> int:
    """Автовход в GitHub (device flow): login/status/logout."""
    from ..auth import load_token, login, logout, status

    try:
        if args.action == "login":
            resume = Path(args.resume) if args.resume else None
            result = login(open_browser=not args.no_browser, resume=resume)
        elif args.action == "status":
            result = status()
        elif args.action == "logout":
            result = logout()
        else:
            print(f"неизвестное действие: {args.action}", file=sys.stderr)
            return 2
    except RuntimeError as exc:
        print(f"[factory auth] ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_release(args) -> int:
    """Собрать Windows-версию и выпустить GitHub Release автоматически."""
    from ..release import find_token, release

    token = args.token or find_token()
    if not token:
        print("[factory release] нет токена GitHub. Сначала: `factory auth login`",
              file=sys.stderr)
        return 2
    try:
        result = release(token, tag=args.tag, draft=args.draft,
                         skip_build=args.no_build, repo=args.repo)
    except RuntimeError as exc:
        print(f"[factory release] ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
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

    p_doctor = sub.add_parser("doctor", help="диагностика адаптеров и зависимостей")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_region = sub.add_parser("region", help="регион/доступ через region-spoof")
    p_region.add_argument("action", nargs="?", default="status", choices=["status", "switch"])
    p_region.add_argument("--region", default=None, help="код региона, напр. US, DE, NL")
    p_region.add_argument("--zone", default=None, help="foreign | local")
    p_region.set_defaults(func=_cmd_region)

    p_jobs = sub.add_parser("jobs", help="очередь задач")
    p_jobs.set_defaults(func=_cmd_jobs)

    p_auth = sub.add_parser("auth", help="автовход в GitHub (device flow)")
    p_auth.add_argument("action", nargs="?", default="status",
                        choices=["login", "status", "logout"])
    p_auth.add_argument("--no-browser", action="store_true",
                        help="не открывать браузер автоматически")
    p_auth.add_argument("--resume", default=None,
                        help="продолжить уже начатый flow (файл с device_code)")
    p_auth.set_defaults(func=_cmd_auth)

    p_release = sub.add_parser("release", help="собрать exe/zip и выпустить GitHub Release")
    p_release.add_argument("--tag", default=None,
                           help="тег (по умолчанию v<версия из pyproject.toml>)")
    p_release.add_argument("--draft", action="store_true", help="черновик релиза")
    p_release.add_argument("--no-build", action="store_true",
                           help="не пересобирать exe (использовать готовый dist/)")
    p_release.add_argument("--repo", default="Lab-100/factory-core",
                           help="репозиторий owner/name")
    p_release.add_argument("--token", default=None,
                           help="токен (по умолчанию: GITHUB_TOKEN / state/github.token / gh)")
    p_release.set_defaults(func=_cmd_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)
