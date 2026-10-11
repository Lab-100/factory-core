"""Трей-интерфейс фабрики (опционально: pip install .[tray])."""
from __future__ import annotations

import threading

from ..domains import DomainRegistry
from ..i18n import make_translator
from ..paths import repo_root
from ..settings import load as load_settings


def _context():
    root = repo_root()
    settings = load_settings(root / "config" / "settings.toml")
    domains_file = root / str(settings.get("domains_file", "config/domains.toml"))
    profiles_dir = root / str(settings.get("profiles_dir", "config/profiles"))
    active = str(settings.get("active_profile", "default"))
    overlay = profiles_dir / f"{active}.toml"
    registry = DomainRegistry(domains_file, [overlay] if overlay.exists() else [])
    return settings, registry


def serve() -> int:
    settings, registry = _context()
    tr = make_translator(settings)
    try:
        import pystray  # type: ignore
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:  # noqa: BLE001
        print(tr("tray.unavailable"))
        return 1

    image = Image.new("RGB", (64, 64), "#161a22")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 24, 48, 52), fill="#2d6cdf")
    draw.rectangle((24, 12, 40, 24), fill="#5ddc7d")

    def open_web(icon, item):  # noqa: ANN001, ARG001
        from .web import serve as serve_web
        threading.Thread(target=serve_web, daemon=True).start()

    def toggle_automation(icon, item):  # noqa: ANN001, ARG001
        settings.set("automation.mode", "semi" if settings.automation == "auto" else "auto")
        settings.save()

    def reload_domains(icon, item):  # noqa: ANN001, ARG001
        registry.reload()

    def show_quotas(icon, item):  # noqa: ANN001, ARG001
        from .. import quotas
        providers = (quotas.build(settings).get("providers")) or {}
        if not providers:
            icon.notify(tr("quotas.empty"), tr("tray.quotas"))
            return
        lines = [
            tr("tray.quota_line", provider=provider,
               remaining=("∞" if row["remaining"] is None else row["remaining"]))
            for provider, row in providers.items()
        ]
        icon.notify("\n".join(lines), tr("tray.quotas"))

    def show_inventory(icon, item):  # noqa: ANN001, ARG001
        from .. import inventory
        summary = inventory.build(settings)["summary"]
        icon.notify(tr("tray.inventory_line", present=summary["repos_present"],
                       total=summary["repos_total"], tools=summary["tools"]),
                    tr("tray.inventory"))

    def quit_app(icon, item):  # noqa: ANN001, ARG001
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: tr("tray.automation", mode=settings.automation), toggle_automation),
        pystray.MenuItem(tr("tray.open_web"), open_web),
        pystray.MenuItem(tr("tray.reload"), reload_domains),
        pystray.MenuItem(tr("tray.quotas"), show_quotas),
        pystray.MenuItem(tr("tray.inventory"), show_inventory),
        pystray.MenuItem(tr("tray.quit"), quit_app),
    )
    icon = pystray.Icon("factory-core", image, "Фабрика Lab-100", menu)
    print(tr("tray.started"))
    icon.run()
    return 0
