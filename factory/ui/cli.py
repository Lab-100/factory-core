"""CLI фабрики Lab-100 (интерфейсы на русском/английском/китайском, по умолчанию русский)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import regionlib
from ..context import build_context
from ..dispatch import Dispatcher
from ..doctor import run_doctor, summarize
from ..i18n import choose, make_translator, t
from ..task import Task


def _cmd_status(args) -> int:
    root, settings, registry, ledger = build_context()
    tr = make_translator(settings)
    enabled = registry.enabled()
    checkpoints = repr(settings.checkpoints if settings.checkpoints else [])
    automation_value = (
        tr("status.v_auto", checkpoints=checkpoints)
        if settings.automation == "auto"
        else tr("status.v_semi", checkpoints=checkpoints)
    )
    print(f"{tr('status.root')} : {root}")
    print(f"{tr('status.profile')}  : {settings.get('active_profile', 'default')}")
    print(f"{tr('status.automation')}  : {automation_value}")
    print(f"{tr('status.ui')}       : {settings.ui}")
    print(f"{tr('status.domains')}      : "
          f"{tr('status.enabled_of', enabled=len(enabled), total=len(registry.all()))}")
    for dom in registry.all():
        mark = "[+]" if dom.enabled else "[-]"
        print(f"  {mark} {dom.name:<12} p{dom.priority:<3} {dom.title}")
    recent = ledger.tail(5)
    print(f"{tr('status.ledger')}       : {ledger.path} "
          f"({tr('status.last_events', count=len(recent))})")
    for event in recent:
        print(f"  · {event.get('kind')} {event.get('task', '')} {event.get('status', '')}")
    return 0


def _cmd_domains(args) -> int:
    _, settings, registry, _ = build_context()
    tr = make_translator(settings)
    if args.action in ("list", None):
        for dom in registry.all():
            mark = "[+]" if dom.enabled else "[-]"
            caps = ", ".join(dom.capabilities)
            print(f"{mark} {dom.name:<12} p{dom.priority:<3} [{caps}]  {dom.title}")
    elif args.action == "show":
        dom = registry.get(args.name)
        if not dom:
            print(tr("err.domain_not_found", name=args.name), file=sys.stderr)
            return 1
        print(json.dumps(dom.to_dict(), ensure_ascii=False, indent=2))
    elif args.action in ("enable", "disable"):
        dom = registry.set_enabled(args.name, args.action == "enable")
        print(tr("domains.set_enabled", name=dom.name, enabled=dom.enabled))
    elif args.action == "priority":
        dom = registry.set_priority(args.name, args.value)
        print(f"{dom.name}: priority={dom.priority}")
    else:
        print(tr("err.unknown_action", action=args.action), file=sys.stderr)
        return 2
    return 0


def _cmd_settings(args) -> int:
    _, settings, _, _ = build_context()
    tr = make_translator(settings)
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
        print(tr("err.unknown_action", action=args.action), file=sys.stderr)
        return 2
    return 0


def _cmd_run(args) -> int:
    root, settings, registry, ledger = build_context()
    tr = make_translator(settings)
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
            answer = input(tr("run.confirm_step", id=step.id, cap=step.capability,
                              adapter=step.adapter, action=step.action))
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
    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    print(tr("ui.cli_hint"))
    return 0


def _cmd_doctor(args) -> int:
    """Диагностика: какие исполнители и зависимости доступны."""
    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    rows = run_doctor(settings)
    for row in rows:
        mark = "[ok]" if row.get("available") else "[--]"
        print(f"{mark} {row.get('adapter')}")
        for key, value in row.items():
            if key in ("adapter", "available") or value in (None, [], {}, ""):
                continue
            print(f"      {key}: {value}")
    ready, total = summarize(rows)
    print(f"\n{tr('doctor.ready', ready=ready, total=total)}")
    if ready < total:
        print(tr("doctor.hint"))
    return 0 if ready else 1


def _cmd_region(args) -> int:
    """Управление регионом через region-spoof (протокол см. docs/REGION_PROTOCOL.md)."""
    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    repo = regionlib.find_repo(settings)
    if repo is None:
        print(tr("region.not_found"))
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
            data["note"] = tr("region.note")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(tr("err.unknown_action", action=args.action), file=sys.stderr)
    return 2


def _cmd_jobs(args) -> int:
    """Список задач в очереди фабрики (state/jobs.jsonl)."""
    root, settings, _, _ = build_context()
    from ..jobs import JobManager

    tr = make_translator(settings)
    manager = JobManager(root)
    jobs = manager.list()
    if not jobs:
        print(tr("jobs.empty"))
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
    from ..auth import login, logout, status

    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    try:
        if args.action == "login":
            resume = Path(args.resume) if args.resume else None
            result = login(open_browser=not args.no_browser, resume=resume, translator=tr)
        elif args.action == "status":
            result = status()
        elif args.action == "logout":
            result = logout()
        else:
            print(tr("err.unknown_action", action=args.action), file=sys.stderr)
            return 2
    except RuntimeError as exc:
        print(f"[factory auth] {tr('auth.error')} {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_release(args) -> int:
    """Собрать Windows-версию и выпустить GitHub Release автоматически."""
    from ..release import find_token, release

    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    token = args.token or find_token()
    if not token:
        print(tr("rel.no_token"), file=sys.stderr)
        return 2
    try:
        result = release(token, tag=args.tag, draft=args.draft,
                         skip_build=args.no_build, repo=args.repo, translator=tr)
    except RuntimeError as exc:
        print(f"[factory release] {tr('rel.error')} {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser(lang: str = "ru") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description=t(lang, "parser.desc"))
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help=t(lang, "cmd.status"))
    p_status.set_defaults(func=_cmd_status)

    p_domains = sub.add_parser("domains", help=t(lang, "cmd.domains"))
    p_domains.add_argument("action", nargs="?", default="list",
                           choices=["list", "show", "enable", "disable", "priority"])
    p_domains.add_argument("name", nargs="?", default="")
    p_domains.add_argument("value", nargs="?", type=int, default=50)
    p_domains.set_defaults(func=_cmd_domains)

    p_settings = sub.add_parser("settings", help=t(lang, "cmd.settings"))
    p_settings.add_argument("action", nargs="?", default="list", choices=["list", "get", "set"])
    p_settings.add_argument("key", nargs="?", default="")
    p_settings.add_argument("value", nargs="?", default="")
    p_settings.set_defaults(func=_cmd_settings)

    p_run = sub.add_parser("run", help=t(lang, "cmd.run"))
    p_run.add_argument("file", help=t(lang, "arg.run.file"))
    p_run.add_argument("--dry-run", action="store_true", help=t(lang, "arg.run.dry"))
    p_run.add_argument("--auto", action="store_true", help=t(lang, "arg.run.auto"))
    p_run.add_argument("--semi", action="store_true", help=t(lang, "arg.run.semi"))
    p_run.add_argument("--yes", action="store_true", help=t(lang, "arg.run.yes"))
    p_run.set_defaults(func=_cmd_run)

    p_ui = sub.add_parser("ui", help=t(lang, "cmd.ui"))
    p_ui.add_argument("--mode", choices=["cli", "web", "tray"], default="cli")
    p_ui.set_defaults(func=_cmd_ui)

    p_doctor = sub.add_parser("doctor", help=t(lang, "cmd.doctor"))
    p_doctor.set_defaults(func=_cmd_doctor)

    p_region = sub.add_parser("region", help=t(lang, "cmd.region"))
    p_region.add_argument("action", nargs="?", default="status", choices=["status", "switch"])
    p_region.add_argument("--region", default=None, help=t(lang, "arg.region.region"))
    p_region.add_argument("--zone", default=None, help=t(lang, "arg.region.zone"))
    p_region.set_defaults(func=_cmd_region)

    p_jobs = sub.add_parser("jobs", help=t(lang, "cmd.jobs"))
    p_jobs.set_defaults(func=_cmd_jobs)

    p_auth = sub.add_parser("auth", help=t(lang, "cmd.auth"))
    p_auth.add_argument("action", nargs="?", default="status",
                        choices=["login", "status", "logout"])
    p_auth.add_argument("--no-browser", action="store_true", help=t(lang, "arg.auth.no_browser"))
    p_auth.add_argument("--resume", default=None, help=t(lang, "arg.auth.resume"))
    p_auth.set_defaults(func=_cmd_auth)

    p_release = sub.add_parser("release", help=t(lang, "cmd.release"))
    p_release.add_argument("--tag", default=None, help=t(lang, "arg.release.tag"))
    p_release.add_argument("--draft", action="store_true", help=t(lang, "arg.release.draft"))
    p_release.add_argument("--no-build", action="store_true", help=t(lang, "arg.release.no_build"))
    p_release.add_argument("--repo", default="Lab-100/factory-core", help=t(lang, "arg.release.repo"))
    p_release.add_argument("--token", default=None, help=t(lang, "arg.release.token"))
    p_release.set_defaults(func=_cmd_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    _, settings, _, _ = build_context()
    parser = build_parser(lang=choose(settings))
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)