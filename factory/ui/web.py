"""Веб-дашборд фабрики (стандартный http.server, без зависимостей)."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..dispatch import Dispatcher
from ..domains import DomainRegistry
from ..ledger import Ledger
from ..paths import repo_root
from ..settings import load as load_settings

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Фабрика Lab-100</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:16px 24px;background:#161a22;border-bottom:1px solid #262b36}
 h1{margin:0;font-size:18px}
 main{padding:24px;display:grid;gap:24px}
 section{background:#161a22;border:1px solid #262b36;border-radius:10px;padding:16px}
 table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #262b36}
 .on{color:#5ddc7d}.off{color:#e06c75}
 button{background:#2d6cdf;border:0;color:#fff;padding:6px 10px;border-radius:6px;cursor:pointer}
 code{color:#9ad1ff}
</style></head><body>
<header><h1>🏭 Фабрика Lab-100 — дашборд</h1></header>
<main>
 <section><h2>Настройки</h2><div id="settings"></div></section>
 <section><h2>Подвижные домены</h2><div id="domains"></div></section>
 <section><h2>Журнал</h2><div id="ledger"></div></section>
</main>
<script>
async function j(u){const r=await fetch(u);return r.json()}
async function toggle(name,enabled){await fetch(`/api/domains/toggle?name=${encodeURIComponent(name)}&enabled=${enabled}`);load()}
async function setSetting(k,v){await fetch(`/api/settings/set?key=${encodeURIComponent(k)}&value=${encodeURIComponent(v)}`);load()}
async function load(){
 const s=await j('/api/settings');
 document.getElementById('settings').innerHTML =
  `<p>Профиль: <code>${s.active_profile}</code></p>
   <p>Автоматизация:
     <button onclick="setSetting('automation.mode','auto')">auto</button>
     <button onclick="setSetting('automation.mode','semi')">semi</button>
     &nbsp;текущий: <b>${s.automation}</b></p>
   <p>Интерфейс:
     <button onclick="setSetting('ui.mode','cli')">cli</button>
     <button onclick="setSetting('ui.mode','web')">web</button>
     <button onclick="setSetting('ui.mode','tray')">tray</button>
     <button onclick="setSetting('ui.mode','all')">all</button>
     &nbsp;текущий: <b>${s.ui}</b></p>`;
 const d=await j('/api/domains');
 document.getElementById('domains').innerHTML='<table><tr><th>Домен</th><th>Приоритет</th><th>Компетенции</th><th>Статус</th></tr>'+
  d.domains.map(x=>`<tr><td>${x.name}<br><small>${x.title||''}</small></td><td>${x.priority}</td>
   <td>${x.capabilities.join(', ')}</td>
   <td class="${x.enabled?'on':'off'}">${x.enabled?'вкл':'выкл'}
   <button onclick="toggle('${x.name}',${!x.enabled})">${x.enabled?'выкл':'вкл'}</button></td></tr>`).join('')+'</table>';
 const l=await j('/api/ledger');
 document.getElementById('ledger').innerHTML='<pre>'+l.events.map(e=>JSON.stringify(e)).join('\\n')+'</pre>';
}
load();
</script></body></html>"""


def _context():
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


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        def _send(self, payload, status=200, content_type="application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # тише
            return

        def do_GET(self):  # noqa: N802
            _, settings, registry, ledger = _context()
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            path = parsed.path
            if path == "/":
                return self._send(PAGE.encode("utf-8"), content_type="text/html; charset=utf-8")
            if path == "/api/settings":
                return self._send({
                    "active_profile": settings.get("active_profile", "default"),
                    "automation": settings.automation,
                    "ui": settings.ui,
                    "data": settings.data,
                })
            if path == "/api/settings/set":
                key = query.get("key", [""])[0]
                value = query.get("value", [""])[0]
                settings.set(key, value)
                settings.save()
                return self._send({"ok": True, "key": key, "value": settings.get(key)})
            if path == "/api/domains":
                return self._send({"domains": [d.to_dict() | {"name": d.name} for d in registry.all()]})
            if path == "/api/domains/toggle":
                name = query.get("name", [""])[0]
                enabled = query.get("enabled", ["true"])[0].lower() == "true"
                try:
                    registry.set_enabled(name, enabled)
                except KeyError as exc:
                    return self._send({"ok": False, "error": str(exc)}, status=404)
                registry.persist_overlay(repo_root() / "config" / "profiles" / "_runtime.toml")
                return self._send({"ok": True, "name": name, "enabled": enabled})
            if path == "/api/ledger":
                return self._send({"events": ledger.tail(100)})
            return self._send({"error": "not found"}, status=404)

    return Handler


def serve(host: str | None = None, port: int | None = None) -> int:
    _, settings, _, _ = _context()
    host = host or str(settings.get("ui.web.host", "127.0.0.1"))
    port = port or int(settings.get("ui.web.port", 8787))
    server = ThreadingHTTPServer((host, port), _handler_class())
    print(f"Дашборд фабрики: http://{host}:{port}  (Ctrl+C — выход)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
    return 0
