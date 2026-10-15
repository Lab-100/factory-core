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
from ..sessions import SessionStore
from ..selfheal import SelfHealer
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


def _runtime_overlay(root: Path, settings) -> Path:
    """Путь рантайм-профиля доменов (config/profiles/_runtime.toml)."""
    return root / str(settings.get("profiles_dir", "config/profiles")) / "_runtime.toml"


def _cmd_domains(args) -> int:
    root, settings, registry, _ = build_context()
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
        try:
            dom = registry.set_enabled(args.name, args.action == "enable")
        except KeyError:
            print(tr("err.domain_not_found", name=args.name), file=sys.stderr)
            return 1
        registry.persist_overlay(_runtime_overlay(root, settings))
        print(tr("domains.set_enabled", name=dom.name, enabled=dom.enabled))
    elif args.action == "priority":
        try:
            dom = registry.set_priority(args.name, args.value)
        except KeyError:
            print(tr("err.domain_not_found", name=args.name), file=sys.stderr)
            return 1
        registry.persist_overlay(_runtime_overlay(root, settings))
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
    session_id = str(getattr(args, "session", "") or "")
    store = SessionStore(root) if session_id else None
    if store is not None:
        store.ensure(session_id)
        store.append(session_id, "user", task.goal)

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
    if store is not None:
        store.append(session_id, "assistant",
                     tr("sessions.done") if result.get("ok") else tr("sessions.failed"))
    return 0 if result["ok"] or args.dry_run else 1


def _cmd_ui(args) -> int:
    """Интерфейс фабрики: окно, дашборд в браузере или трей.

    По умолчанию интерфейс поднимается один раз: повторный запуск (например,
    второй клик по ярлыку) не создаёт второго окна и сервера — он сообщает о
    работающем экземпляре и выводит его окно на передний план. Несколько
    экземпляров разрешает оператор флагом ``--allow-multiple``.
    """
    mode = args.mode
    if mode == "cli":
        _, settings, _, _ = build_context()
        tr = make_translator(settings)
        print(tr("ui.cli_hint"))
        return 0

    root, settings, _, _ = build_context()
    tr = make_translator(settings)
    from ..single import InstanceGuard, open_dashboard

    allow = bool(getattr(args, "allow_multiple", False)) or bool(
        settings.get("ui.allow_multiple", False))
    want_open = bool(getattr(args, "open", False))
    guard = InstanceGuard(root=root, allow_multiple=allow, mode=mode)
    if guard.acquire():
        owner = guard.owner()
        focused = guard.focus()
        url = str(owner.get("url") or "")
        print(tr("ui.already_running", pid=owner.get("pid", "—"),
                 mode=owner.get("mode") or mode))
        if focused:
            print(tr("ui.already_focused", pid=focused))
        elif not url:
            print(tr("ui.already_no_focus"))
        elif want_open:
            how = open_dashboard(url)
            if how == "reused":
                print(tr("ui.already_browser_reused", url=url))
            elif how == "opened":
                print(tr("ui.already_browser_opened", url=url))
            else:
                print(tr("ui.already_no_focus"))
        else:
            # Новых вкладок не плодим: открытая страница обновляется сама.
            print(tr("ui.already_no_open", url=url))
        print(tr("ui.allow_multiple_hint", mode=mode))
        return 0
    if guard.degraded:
        print(tr("ui.guard_degraded"))
    try:
        if mode == "web":
            from .web import serve
            return serve(open_browser=want_open)
        if mode == "tray":
            from .tray import serve
            return serve()
        from .desktop import serve
        return serve(open_browser=want_open)
    finally:
        guard.release()


def _print_health(row: dict, name_key: str) -> None:
    """Печать одной строки диагностики: маркер, имя и подробности."""
    mark = "[ok]" if row.get("available") else "[--]"
    print(f"{mark} {row.get(name_key)}")
    for key, value in row.items():
        if key in (name_key, "available") or value in (None, [], {}, ""):
            continue
        print(f"      {key}: {value}")


def _cmd_doctor(args) -> int:
    """Диагностика: какие исполнители, интеграции и зависимости доступны."""
    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    data = run_doctor(settings)
    adapters = data["adapters"]
    integrations = data["integrations"]
    print(tr("doctor.adapters"))
    for row in adapters:
        _print_health(row, "adapter")
    ready, total = summarize(adapters)
    print(tr("doctor.integrations"))
    for row in integrations:
        _print_health(row, "integration")
    ready_int = sum(1 for row in integrations if row.get("available"))
    print(f"\n{tr('doctor.ready', ready=ready, total=total)}")
    print(tr("doctor.integrations_ready", ready=ready_int, total=len(integrations)))
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


def _fmt_num(value) -> str:
    """Число без хвостового .0; None → знак бесконечности."""
    if value is None:
        return "∞"
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(as_float)) if as_float.is_integer() else str(as_float)


def _inventory_detail(name: str, source: dict) -> str:
    if name == "scripts-tools":
        return f"tools={source.get('count', 0)}, shims={len(source.get('shims', []))}"
    if name == "Lab100":
        return f"providers={source.get('count', 0)}"
    if name == "region-spoof":
        return f"regions={len(source.get('regions', []))}"
    return f"stages={source.get('count', 0)}"


def _ledger_detail(event: dict) -> str:
    parts: list[str] = []
    if event.get("task"):
        parts.append(f"task={event['task']}")
    if event.get("step") is not None:
        parts.append(f"step={event['step']}")
    if event.get("status"):
        parts.append(f"status={event['status']}")
    elif event.get("ok") is not None:
        parts.append(f"ok={event['ok']}")
    if event.get("adapter"):
        parts.append(f"adapter={event['adapter']}")
    if event.get("provider"):
        parts.append(f"provider={event['provider']}")
    if event.get("sink"):
        parts.append(f"sink={event['sink']}")
    if event.get("change_id"):
        parts.append(f"change={event['change_id']}")
    if event.get("catalog"):
        parts.append(f"catalog={event['catalog']}")
    if event.get("actions"):
        parts.append("actions=" + ",".join(str(a.get("action", "?")) for a in event["actions"]))
    return ", ".join(parts)


def _cmd_inventory(args) -> int:
    """Инвентаризация: карта инструментов/провайдеров/регионов (этап 0)."""
    from .. import inventory

    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    data = inventory.build(settings)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(tr("inventory.title"))
        print(tr("inventory.summary", **data["summary"]))
        for name, source in data["sources"].items():
            if not source.get("available"):
                print(tr("inventory.absent", name=name))
                if source.get("hint"):
                    print("    " + tr("inventory.hint", hint=source["hint"]))
            else:
                print(tr("inventory.source", name=name, path=source.get("root", ""),
                         detail=_inventory_detail(name, source)))
    if not args.no_save:
        saved = inventory.save(settings, data)
        print(tr("inventory.saved", path=saved))
    return 0


def _cmd_quotas(args) -> int:
    """Квоты исполнителей Lab100: лимиты + расход (этап 4)."""
    from .. import quotas

    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    if args.reset is not None:
        quotas.reset(settings, args.reset or None)
        print(tr("quotas.reset_ok"))
        return 0
    data = quotas.build(settings)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(tr("quotas.title"))
    if not data["available"]:
        print(tr("quotas.not_available"))
        return 0
    if not data["providers"]:
        print(tr("quotas.empty"))
        return 0
    header = (f"{tr('quotas.th_provider'):<16} {tr('quotas.th_window'):<10} "
              f"{tr('quotas.th_limit'):>8} {tr('quotas.th_used'):>14} "
              f"{tr('quotas.th_remaining'):>10} {tr('quotas.th_percent'):>10}")
    print(f"  {header}")
    for provider, row in data["providers"].items():
        percent = "" if row["percent"] is None else row["percent"]
        print(f"  {provider:<16} {str(row['window']):<10} "
              f"{_fmt_num(row['limit']):>8} {_fmt_num(row['used']):>14} "
              f"{_fmt_num(row['remaining']):>10} {str(percent):>10}")
    return 0


def _cmd_watchdog(args) -> int:
    """Супервизия фабрики: наблюдение и авто-восстановление (этап 5)."""
    from ..watchdog import Watchdog

    root, settings, _, ledger = build_context()
    tr = make_translator(settings)
    dog = Watchdog(settings, root=root, ledger=ledger)
    iterations = 1 if args.once else (None if not args.iterations else args.iterations)
    print(tr("watchdog.title"))
    counter = {"n": 0}

    def on_tick(result: dict) -> None:
        counter["n"] += 1
        health = result.get("health", {})
        actions = result.get("actions", [])
        active = sum(1 for value in health.values() if value)
        print(tr("watchdog.tick", n=counter["n"], active=active,
                 total=len(health), actions=len(actions)))
        if actions:
            for action in actions:
                detail = ", ".join(f"{k}={v}" for k, v in action.items() if k != "action")
                print(tr("watchdog.action", action=action.get("action"), detail=detail))
        else:
            print(tr("watchdog.none"))

    try:
        dog.run(interval=args.interval, iterations=iterations, on_tick=on_tick)
    except KeyboardInterrupt:
        print()
    if iterations is None:
        print(tr("watchdog.stopped", n=counter["n"]))
    return 0


def _cmd_ledger(args) -> int:
    """Единый журнал фабрики: задачи/шаги/изменения/watchdog (§8.4)."""
    _, settings, _, ledger = build_context()
    tr = make_translator(settings)
    events = ledger.tail(args.limit)
    if args.kind:
        events = [event for event in events if str(event.get("kind", "")).startswith(args.kind)]
    if not events:
        print(tr("ledger.empty"))
        return 0
    print(tr("ledger.title", count=len(events)))
    for event in events:
        print(tr("ledger.line", ts=event.get("ts", ""), kind=event.get("kind", ""),
                 detail=_ledger_detail(event)))
    return 0


def _cmd_manifest(args) -> int:
    """Манифест фабрики `project.json` (invr.project/1) — §8.1."""
    root, settings, _, _ = build_context()
    tr = make_translator(settings)
    path = Path(root) / "project.json"
    if not path.exists():
        print(tr("manifest.missing"), file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_catalog(args) -> int:
    """Каталог артефактов: результаты задач по доменам (§5.6)."""
    from .. import catalog

    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    if args.action == "show":
        if not args.path:
            print(tr("catalog.need_path"), file=sys.stderr)
            return 2
        text = catalog.report(settings, args.path)
        if text is None:
            print(tr("catalog.not_found", path=args.path), file=sys.stderr)
            return 1
        print(text, end="" if text.endswith("\n") else "\n")
        return 0
    data = catalog.build(settings)
    items = data["items"]
    if args.domain:
        items = [item for item in items if item["domain"] == args.domain]
    if args.json:
        data["items"] = items
        data["count"] = len(items)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(tr("catalog.title", count=len(items), base=data["base"]))
    if not items:
        print(tr("catalog.empty"))
        return 0
    for item in items:
        mark = "[ok]" if item["ok"] else ("[--]" if item["stopped"] else "[!!]")
        print(tr("catalog.line", mark=mark, domain=item["domain"] or "-",
                 name=item["name"], steps=item["steps"],
                 goal=str(item["goal"])[:60]))
    return 0


def _cmd_arsenal(args) -> int:
    """Арсенал: единый каталог элементов и их состояний (пульт)."""
    from ..supervisor import Supervisor

    root, settings, _, ledger = build_context()
    tr = make_translator(settings)
    sup = Supervisor(settings, root=root, ledger=ledger)
    data = sup.snapshot(save=not args.no_save)
    nodes_list = data["nodes"]
    if args.klass:
        nodes_list = [node for node in nodes_list if node["class"] == args.klass]
    if args.json:
        print(json.dumps({**data, "nodes": nodes_list}, ensure_ascii=False, indent=2))
        return 0
    summary = data["summary"]
    print(tr("arsenal.title", mode=data["mode"], working=summary["working"],
             total=summary["total"], attention=summary["attention"]))
    if not nodes_list:
        print(tr("arsenal.empty"))
        return 0
    for node in nodes_list:
        print(tr("arsenal.line", mark=_state_mark(node["state"]), state=node["state"],
                 klass=node["class"], name=node["name"], detail=node.get("detail") or ""))
    return 0


def _state_mark(state: str) -> str:
    return {
        "active": "[ok]", "idle": "[--]", "unloaded": "[..]",
        "need-guard": "[!!]", "fault": "[XX]", "unknown": "[??]",
    }.get(state, "[??]")


def _cmd_supervise(args) -> int:
    """Супервизор арсенала: наблюдение и авто-восстановление (настройкой)."""
    from ..supervisor import Supervisor

    root, settings, _, ledger = build_context()
    tr = make_translator(settings)
    sup = Supervisor(settings, root=root, ledger=ledger)
    if args.no_heal:
        sup._no_heal = True
    iterations = 1 if args.once else (None if not args.iterations else args.iterations)
    interval = args.interval if args.interval is not None else float(
        settings.get("supervision.interval_s", 30))
    print(tr("supervise.title", mode=sup.mode,
             autostart=("on" if sup.autostart else "off")))
    counter = {"n": 0}

    def on_tick(result: dict) -> None:
        counter["n"] += 1
        summary = result.get("summary", {})
        print(tr("supervise.tick", n=counter["n"], working=summary.get("working", 0),
                 total=summary.get("total", 0), attention=summary.get("attention", 0),
                 actions=len(result.get("actions", []))))
        for action in result.get("actions", []):
            detail = ", ".join(f"{k}={v}" for k, v in action.items() if k != "action")
            print(tr("supervise.action", action=action.get("action"), detail=detail))

    try:
        sup.run(interval=interval, iterations=iterations, on_tick=on_tick)
    except KeyboardInterrupt:
        print()
    if iterations is None:
        print(tr("supervise.stopped", n=counter["n"]))
    return 0


def _cmd_logs(args) -> int:
    """Журналы арсенала: по каждому элементу и сводно."""
    from .. import logs as logmod

    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    if args.sources:
        data = {"sources": logmod.sources(settings)}
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        print(tr("logs.sources_title"))
        for source in data["sources"]:
            mark = "[ok]" if source["available"] else "[--]"
            print(tr("logs.source_line", mark=mark, id=source["id"],
                     path=source["path"]))
        return 0
    limit = args.limit or int(settings.get("logs.limit_default", 200))
    data = logmod.tail(settings, source=args.source, limit=limit,
                       element=args.element, level=args.level)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not data["lines"]:
        print(tr("logs.empty"))
        return 0
    print(tr("logs.title", count=data["count"], source=args.source or "all"))
    for item in data["lines"]:
        print(tr("logs.line", source=item["source"], line=item["line"]))
    return 0


def _cmd_monitor(args) -> int:
    """Микромонитор запуска: перехват вывода, ошибок и кода возврата."""
    from ..monitor import LaunchMonitor

    root, settings, _, _ = build_context()
    tr = make_translator(settings)

    target = list(args.target or [])
    if target and target[0] == "--":
        target = target[1:]
    command = target or None

    timeout = args.timeout if args.timeout else float(settings.get("monitor.timeout_s", 0) or 0)
    monitor = LaunchMonitor(
        command, root=root,
        log_path=args.log or settings.get("monitor.log") or None,
        errors_path=args.errors or settings.get("monitor.errors_log") or None,
    )
    if not args.json:
        print(tr("monitor.running", command=" ".join(monitor.command)))
    report = monitor.run(timeout=timeout or None)

    if args.json:
        payload = {key: value for key, value in report.items() if key != "errors"}
        payload["errors_count"] = len(report["errors"])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2

    print(tr("monitor.finished", code=report["returncode"], duration=report["duration"]))
    if args.all:
        print(monitor.to_text())
    if report["errors"]:
        print(tr("monitor.errors_title", count=len(report["errors"])))
        print(report["report"])
    else:
        print(tr("monitor.no_errors"))
    print(tr("monitor.log_path", path=report["log"]))
    print(tr("monitor.errors_path", path=report["errors_log"]))
    return 0 if report["ok"] else 2


def _cmd_sessions(args) -> int:
    """Сессии: история запросов и ответов по задачам."""
    root, settings, _, _ = build_context()
    tr = make_translator(settings)
    store = SessionStore(root)
    action = getattr(args, "session_action", None) or "list"
    if action == "new":
        session = store.create(title=getattr(args, "title", "") or "",
                               project=str(settings.get("project", "") or ""))
        print(tr("sessions.created", id=session["id"], title=session["title"]))
        return 0
    if action == "add":
        if not args.text:
            print(tr("sessions.need_text"))
            return 1
        if store.append(args.id, "user", args.text) is None:
            print(tr("sessions.not_found", id=args.id))
            return 1
        print(tr("sessions.appended", id=args.id))
        return 0
    if action == "rm":
        if store.delete(args.id):
            print(tr("sessions.deleted", id=args.id))
            return 0
        print(tr("sessions.not_found", id=args.id))
        return 1
    if action == "show":
        session = store.get(args.id)
        if session is None:
            print(tr("sessions.not_found", id=args.id))
            return 1
        if args.json:
            print(json.dumps(session, ensure_ascii=False, indent=2))
            return 0
        print(tr("sessions.show_title", id=session["id"], title=session["title"],
                 count=len(session["messages"])))
        for message in session["messages"]:
            role = tr("sessions.role." + str(message.get("role", "system")))
            print(tr("sessions.message", role=role, text=message.get("text", "")))
        return 0
    items = store.list()
    if args.json:
        print(json.dumps({"sessions": items}, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print(tr("sessions.empty"))
        return 0
    print(tr("sessions.title", count=len(items)))
    for item in items:
        print(tr("sessions.line", id=item["id"], title=item["title"], count=item["count"]))
    return 0


def _cmd_export(args) -> int:
    """Экспорт заказа или сессии: markdown, JSON или ZIP."""
    from .. import export as exp

    root, settings, _, _ = build_context()
    tr = make_translator(settings)
    result = exp.export(root, args.kind, args.id, args.format)
    if result is None:
        print(tr("export.not_found", kind=args.kind, id=args.id))
        return 1
    data, _ctype, fname = result
    if not args.out and args.format != "zip":
        sys.stdout.write(data.decode("utf-8", "replace"))
        return 0
    target = Path(args.out) if args.out else root / fname
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(tr("export.saved", path=str(target), size=len(data)))
    return 0


def _cmd_project(args) -> int:
    """Проектные настройки: показать найденный проект или создать файл."""
    from .. import projects

    _root, settings, _, _ = build_context(project=False)
    tr = make_translator(settings)
    action = getattr(args, "project_action", None) or "status"
    if action == "init":
        target = Path(args.path).resolve() if args.path else Path.cwd()
        path = projects.init_project(target, force=args.force)
        print(tr("project.inited", path=str(path)))
        return 0
    found = projects.find_project(args.path or None)
    if action == "show":
        config = Path(args.path).resolve() if args.path else found
        if not config or not Path(config).is_file():
            print(tr("project.none"))
            return 1
        print(tr("project.show_title", path=str(config)))
        print(Path(config).read_text(encoding="utf-8"))
        return 0
    if found:
        print(tr("project.found", path=str(found)))
    else:
        print(tr("project.none"))
    return 0


def _cmd_providers(args) -> int:
    """Внешние ИИ-провайдеры: список, проверка, включение и выключение."""
    from .. import providers

    _root, settings, _, _ = build_context()
    tr = make_translator(settings)
    action = getattr(args, "providers_action", None) or "list"
    name = getattr(args, "name", "") or ""

    if action in ("enable", "disable"):
        if not name:
            print(tr("providers.need_name"))
            return 1
        if not providers.set_enabled(settings, name, action == "enable"):
            print(tr("providers.unknown", name=name))
            return 1
        key = "providers.enabled" if action == "enable" else "providers.disabled"
        print(tr(key, name=providers.title(name)))
        return 0

    items = providers.discover(settings)
    for item in items:
        state = tr("providers.state_on") if item["enabled"] else tr("providers.state_off")
        mark = "[ok]" if item["available"] else "[--]"
        line = f"{mark} {item['name']:<12} {item['title']:<14} {state}"
        if item["command"]:
            line += f" · {item['command']}"
        print(line)
    if action != "check":
        print(tr("providers.hint"))
    return 0


def _cmd_heal(args) -> int:
    """Прогнать тесты и вернуть рабочую копию к зелёному состоянию (self-heal)."""
    root, settings, _, ledger = build_context()
    tr = make_translator(settings)
    healer = SelfHealer(root, settings=settings, ledger=ledger)
    if not healer.git_available():
        print(tr("heal.no_git"))
        return 2
    report = healer.heal(fix=bool(args.fix), timeout=args.timeout, message=args.message)
    if args.commit or args.push:
        report["publish"] = healer.publish(
            push=bool(args.push), message=args.message, wait=args.wait or 0.0,
            interval=healer.wait_interval, timeout=args.timeout)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("healed") else 1
    tests = report.get("tests") or {}
    print(tr("heal.title", action=report.get("action", "")))
    print(tr("heal.tests_ok") if tests.get("ok")
          else tr("heal.tests_failed", failed=tests.get("failed", 0)))
    print(tr("heal.dirty", count=report.get("dirty", 0)))
    if report.get("head"):
        print(tr("heal.head", head=report["head"]))
    if report.get("stash"):
        print(tr("heal.stash", label=report["stash"]))
    action = report.get("action")
    if action == "clean":
        print(tr("heal.clean"))
    elif action == "healed-by-stash":
        print(tr("heal.healed_stash"))
    elif action == "broken-head":
        print(tr("heal.broken_head"))
    elif action == "stash-failed":
        print(tr("heal.stash_failed"))
    publish = report.get("publish")
    if publish:
        print(tr("heal.publish_title", action=publish.get("action", "")))
        if publish.get("waited"):
            print(tr("heal.publish_waited", seconds=publish["waited"]))
        kind = publish.get("action")
        if kind == "tests-red":
            print(tr("heal.publish_tests_red"))
        elif kind == "foreign-busy":
            print(tr("heal.publish_foreign", count=len(publish.get("foreign") or [])))
        elif kind == "commit-failed":
            detail = (publish.get("commit") or {}).get("detail", "")
            print(tr("heal.publish_commit_failed", detail=detail))
        elif kind in ("committed", "pushed"):
            made = publish.get("commit") or {}
            if made.get("committed"):
                print(tr("heal.publish_commit_ok", head=made.get("head", "")))
            if kind == "pushed":
                print(tr("heal.publish_push_ok"))
        elif kind == "push-failed":
            detail = (publish.get("push") or {}).get("detail", "")
            print(tr("heal.publish_push_failed", detail=detail))
        elif kind == "nothing":
            print(tr("heal.publish_nothing"))
    return 0 if report.get("healed") else 1


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
    p_run.add_argument("--session", default="", help=t(lang, "arg.run.session"))
    p_run.set_defaults(func=_cmd_run)

    p_sessions = sub.add_parser("sessions", help=t(lang, "cmd.sessions"))
    p_sessions.add_argument("session_action", nargs="?", default="list",
                            choices=["list", "new", "show", "add", "rm"])
    p_sessions.add_argument("id", nargs="?", default="", help=t(lang, "arg.sessions.id"))
    p_sessions.add_argument("--title", default="", help=t(lang, "arg.sessions.title"))
    p_sessions.add_argument("--text", default="", help=t(lang, "arg.sessions.text"))
    p_sessions.add_argument("--json", action="store_true", help=t(lang, "arg.sessions.json"))
    p_sessions.set_defaults(func=_cmd_sessions)

    p_export = sub.add_parser("export", help=t(lang, "cmd.export"))
    p_export.add_argument("kind", nargs="?", default="job", choices=["job", "session"])
    p_export.add_argument("id", help=t(lang, "arg.export.id"))
    p_export.add_argument("--format", default="md", choices=["md", "json", "zip"])
    p_export.add_argument("--out", default="", help=t(lang, "arg.export.out"))
    p_export.set_defaults(func=_cmd_export)

    p_project = sub.add_parser("project", help=t(lang, "cmd.project"))
    p_project.add_argument("project_action", nargs="?", default="status",
                           choices=["status", "init", "show"])
    p_project.add_argument("--path", default="", help=t(lang, "arg.project.path"))
    p_project.add_argument("--force", action="store_true", help=t(lang, "arg.project.force"))
    p_project.set_defaults(func=_cmd_project)

    p_providers = sub.add_parser("providers", help=t(lang, "cmd.providers"))
    p_providers.add_argument("providers_action", nargs="?", default="list",
                             choices=["list", "check", "enable", "disable"])
    p_providers.add_argument("name", nargs="?", default="", help=t(lang, "arg.providers.name"))
    p_providers.set_defaults(func=_cmd_providers)

    p_ui = sub.add_parser("ui", help=t(lang, "cmd.ui"))
    p_ui.add_argument("--mode", choices=["cli", "web", "tray", "desktop"], default="cli")
    p_ui.add_argument("--allow-multiple", action="store_true",
                      help=t(lang, "arg.ui.allow_multiple"))
    p_ui.add_argument("--open", action="store_true",
                      help=t(lang, "arg.ui.open"))
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

    p_inventory = sub.add_parser("inventory", help=t(lang, "cmd.inventory"))
    p_inventory.add_argument("--json", action="store_true", help=t(lang, "arg.inventory.json"))
    p_inventory.add_argument("--no-save", action="store_true", help=t(lang, "arg.inventory.no_save"))
    p_inventory.set_defaults(func=_cmd_inventory)

    p_quotas = sub.add_parser("quotas", help=t(lang, "cmd.quotas"))
    p_quotas.add_argument("--json", action="store_true", help=t(lang, "arg.quotas.json"))
    p_quotas.add_argument("--reset", nargs="?", const="", default=None,
                          help=t(lang, "arg.quotas.reset"))
    p_quotas.set_defaults(func=_cmd_quotas)

    p_watchdog = sub.add_parser("watchdog", help=t(lang, "cmd.watchdog"))
    p_watchdog.add_argument("--once", action="store_true", help=t(lang, "arg.watchdog.once"))
    p_watchdog.add_argument("--interval", type=float, default=30.0,
                            help=t(lang, "arg.watchdog.interval"))
    p_watchdog.add_argument("--iterations", type=int, default=0,
                            help=t(lang, "arg.watchdog.iterations"))
    p_watchdog.set_defaults(func=_cmd_watchdog)

    p_ledger = sub.add_parser("ledger", help=t(lang, "cmd.ledger"))
    p_ledger.add_argument("--limit", type=int, default=50, help=t(lang, "arg.ledger.limit"))
    p_ledger.add_argument("--kind", default=None, help=t(lang, "arg.ledger.kind"))
    p_ledger.set_defaults(func=_cmd_ledger)

    p_manifest = sub.add_parser("manifest", help=t(lang, "cmd.manifest"))
    p_manifest.set_defaults(func=_cmd_manifest)

    p_catalog = sub.add_parser("catalog", help=t(lang, "cmd.catalog"))
    p_catalog.add_argument("action", nargs="?", default="list", choices=["list", "show"])
    p_catalog.add_argument("path", nargs="?", default="", help=t(lang, "arg.catalog.path"))
    p_catalog.add_argument("--domain", default=None, help=t(lang, "arg.catalog.domain"))
    p_catalog.add_argument("--json", action="store_true", help=t(lang, "arg.catalog.json"))
    p_catalog.set_defaults(func=_cmd_catalog)

    p_arsenal = sub.add_parser("arsenal", help=t(lang, "cmd.arsenal"))
    p_arsenal.add_argument("--class", dest="klass", default=None,
                           choices=["repo", "tool", "agent", "service", "env"],
                           help=t(lang, "arg.arsenal.class"))
    p_arsenal.add_argument("--json", action="store_true", help=t(lang, "arg.arsenal.json"))
    p_arsenal.add_argument("--no-save", action="store_true", help=t(lang, "arg.arsenal.no_save"))
    p_arsenal.set_defaults(func=_cmd_arsenal)

    p_supervise = sub.add_parser("supervise", help=t(lang, "cmd.supervise"))
    p_supervise.add_argument("--once", action="store_true", help=t(lang, "arg.supervise.once"))
    p_supervise.add_argument("--interval", type=float, default=None,
                             help=t(lang, "arg.supervise.interval"))
    p_supervise.add_argument("--iterations", type=int, default=0,
                             help=t(lang, "arg.supervise.iterations"))
    p_supervise.add_argument("--no-heal", action="store_true", help=t(lang, "arg.supervise.no_heal"))
    p_supervise.set_defaults(func=_cmd_supervise)

    p_logs = sub.add_parser("logs", help=t(lang, "cmd.logs"))
    p_logs.add_argument("--source", default=None, help=t(lang, "arg.logs.source"))
    p_logs.add_argument("--element", default=None, help=t(lang, "arg.logs.element"))
    p_logs.add_argument("--level", default=None, help=t(lang, "arg.logs.level"))
    p_logs.add_argument("--limit", type=int, default=0, help=t(lang, "arg.logs.limit"))
    p_logs.add_argument("--sources", action="store_true", help=t(lang, "arg.logs.sources"))
    p_logs.add_argument("--json", action="store_true", help=t(lang, "arg.logs.json"))
    p_logs.set_defaults(func=_cmd_logs)

    p_heal = sub.add_parser("heal", help=t(lang, "cmd.heal"))
    p_heal.add_argument("--fix", action="store_true", help=t(lang, "arg.heal.fix"))
    p_heal.add_argument("--commit", action="store_true", help=t(lang, "arg.heal.commit"))
    p_heal.add_argument("--push", action="store_true", help=t(lang, "arg.heal.push"))
    p_heal.add_argument("--timeout", type=float, default=None,
                        help=t(lang, "arg.heal.timeout"))
    p_heal.add_argument("--wait", type=float, default=0.0,
                        help=t(lang, "arg.heal.wait"))
    p_heal.add_argument("--message", default=None, help=t(lang, "arg.heal.message"))
    p_heal.add_argument("--json", action="store_true", help=t(lang, "arg.heal.json"))
    p_heal.set_defaults(func=_cmd_heal)

    p_monitor = sub.add_parser("monitor", help=t(lang, "cmd.monitor"))
    p_monitor.add_argument("target", nargs=argparse.REMAINDER,
                           help=t(lang, "arg.monitor.target"))
    p_monitor.add_argument("--timeout", type=float, default=None,
                           help=t(lang, "arg.monitor.timeout"))
    p_monitor.add_argument("--log", default=None, help=t(lang, "arg.monitor.log"))
    p_monitor.add_argument("--errors", default=None, help=t(lang, "arg.monitor.errors"))
    p_monitor.add_argument("--all", action="store_true", help=t(lang, "arg.monitor.all"))
    p_monitor.add_argument("--json", action="store_true", help=t(lang, "arg.monitor.json"))
    p_monitor.set_defaults(func=_cmd_monitor)

    return parser


def main(argv: list[str] | None = None) -> int:
    _, settings, _, _ = build_context()
    parser = build_parser(lang=choose(settings))
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)