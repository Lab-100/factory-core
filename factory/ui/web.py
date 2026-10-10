"""Веб-дашборд фабрики (стандартный http.server, без зависимостей).

Эндпоинты:
  GET  /                          — дашборд
  GET  /api/settings              — настройки
  GET  /api/settings/set?key=&value=
  GET  /api/domains               — домены
  GET  /api/domains/toggle?name=&enabled=
  GET  /api/ledger                — журнал
  GET  /api/jobs                  — очередь задач
  GET  /api/jobs/get?id=
  POST /api/run                   — запустить задачу (JSON invr.task/1)
  POST /api/jobs/approve          — решение по шагу: {"id": ..., "ok": true|false}
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..context import build_context
from ..jobs import JobManager
from ..paths import repo_root

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Фабрика Lab-100</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:16px 24px;background:#161a22;border-bottom:1px solid #262b36}
 h1{margin:0;font-size:18px}
 main{padding:24px;display:grid;gap:24px;max-width:1100px;margin:0 auto}
 section{background:#161a22;border:1px solid #262b36;border-radius:10px;padding:16px}
 table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #262b36}
 .on{color:#5ddc7d}.off{color:#e06c75}.wait{color:#e5c07b}
 button{background:#2d6cdf;border:0;color:#fff;padding:6px 10px;border-radius:6px;cursor:pointer;margin:2px}
 button.warn{background:#c0392b}
 textarea,input,select{background:#0f1115;color:#e6e6e6;border:1px solid #3a4250;border-radius:6px;padding:8px;width:100%}
 textarea{resize:vertical}
 label{display:inline-block;margin-right:12px;margin-top:8px}
 code{color:#9ad1ff}
</style></head><body>
<header><h1>🏭 Фабрика Lab-100 — дашборд</h1></header>
<main>
 <section><h2>Новая задача</h2>
  <textarea id="goal" rows="3" placeholder="Что нужно сделать?"></textarea>
  <label>Домен: <input id="domain" placeholder="auto"></label>
  <label>Режим:
   <select id="automation">
    <option value="">из настроек</option><option value="auto">auto</option><option value="semi">semi</option>
   </select></label>
  <label><input type="checkbox" id="dry"> dry-run</label><br>
  <button onclick="submitTask()">Запустить</button>
  <div id="submitMsg"></div>
 </section>
 <section><h2>Задачи</h2><div id="jobs"></div></section>
 <section><h2>Настройки</h2><div id="settings"></div></section>
 <section><h2>Подвижные домены</h2><div id="domains"></div></section>
 <section><h2>Журнал</h2><div id="ledger"></div></section>
</main>
<script>
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function j(u){const r=await fetch(u);return r.json()}
async function toggle(name,enabled){await fetch(`/api/domains/toggle?name=${encodeURIComponent(name)}&enabled=${enabled}`);load()}
async function setSetting(k,v){await fetch(`/api/settings/set?key=${encodeURIComponent(k)}&value=${encodeURIComponent(v)}`);load()}
async function submitTask(){
 const goal=document.getElementById('goal').value.trim();
 if(!goal){document.getElementById('submitMsg').textContent='Укажите цель задачи';return}
 const body={
  schema:'invr.task/1',
  goal,
  domain:document.getElementById('domain').value.trim()||null,
  automation:document.getElementById('automation').value||null,
  dry_run:document.getElementById('dry').checked
 };
 const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const d=await r.json();
 document.getElementById('submitMsg').textContent=d.ok?`Задача ${d.job.id} запущена (${d.job.status})`:('Ошибка: '+(d.error||''));
 loadJobs();
}
async function approveJob(id,ok){
 await fetch('/api/jobs/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,ok})});
 loadJobs();
}
async function loadJobs(){
 const r=await j('/api/jobs');
 document.getElementById('jobs').innerHTML='<table><tr><th>id</th><th>статус</th><th>цель</th><th>домен</th><th></th></tr>'+
  r.jobs.map(x=>{
   let actions='';
   if(x.awaiting) actions=`<button onclick="approveJob('${x.id}',true)">✓ выполнить</button> <button class="warn" onclick="approveJob('${x.id}',false)">✕ пропустить</button>`;
   const cls=x.status==='awaiting-approval'?'wait':(x.status==='done'?'on':(x.status==='running'?'wait':'off'));
   return `<tr><td>${x.id}</td><td class="${cls}">${x.status}</td><td>${esc(x.goal).slice(0,60)}</td><td>${x.domain||'auto'}</td><td>${actions}</td></tr>`;
  }).join('')+'</table>';
}
async function load(){
 const s=await j('/api/settings');
 document.getElementById('settings').innerHTML =
  `<p>Профиль: <code>${esc(s.active_profile)}</code></p>
   <p>Автоматизация:
     <button onclick="setSetting('automation.mode','auto')">auto</button>
     <button onclick="setSetting('automation.mode','semi')">semi</button>
     &nbsp;текущий: <b>${esc(s.automation)}</b></p>
   <p>Интерфейс:
     <button onclick="setSetting('ui.mode','cli')">cli</button>
     <button onclick="setSetting('ui.mode','web')">web</button>
     <button onclick="setSetting('ui.mode','tray')">tray</button>
     <button onclick="setSetting('ui.mode','all')">all</button>
     &nbsp;текущий: <b>${esc(s.ui)}</b></p>`;
 const d=await j('/api/domains');
 document.getElementById('domains').innerHTML='<table><tr><th>Домен</th><th>Приоритет</th><th>Компетенции</th><th>Статус</th></tr>'+
  d.domains.map(x=>`<tr><td>${esc(x.name)}<br><small>${esc(x.title||'')}</small></td><td>${x.priority}</td>
   <td>${x.capabilities.map(esc).join(', ')}</td>
   <td class="${x.enabled?'on':'off'}">${x.enabled?'вкл':'выкл'}
   <button onclick="toggle('${x.name}',${!x.enabled})">${x.enabled?'выкл':'вкл'}</button></td></tr>`).join('')+'</table>';
 const l=await j('/api/ledger');
 document.getElementById('ledger').innerHTML='<pre>'+l.events.map(e=>esc(JSON.stringify(e))).join('\\n')+'</pre>';
}
load();loadJobs();
setInterval(loadJobs,2000);
</script></body></html>"""

_MANAGER: JobManager | None = None
_MANAGER_LOCK = threading.Lock()


def _jobs() -> JobManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = JobManager(repo_root())
    return _MANAGER


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
            _, settings, registry, ledger = build_context()
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
            if path == "/api/jobs":
                return self._send({"jobs": [_jobs().summary(j) for j in _jobs().list()]})
            if path == "/api/jobs/get":
                job = _jobs().get(query.get("id", [""])[0])
                return self._send(job or {"error": "not found"}, status=200 if job else 404)
            return self._send({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._send({"ok": False, "error": "bad json"}, status=400)
            path = parsed.path
            if path == "/api/run":
                job = _jobs().submit(data)
                return self._send({"ok": True, "job": _jobs().summary(job)})
            if path == "/api/jobs/approve":
                job = _jobs().approve(data.get("id"), ok=bool(data.get("ok", True)))
                return self._send({
                    "ok": job is not None,
                    "job": _jobs().summary(job) if job else None,
                }, status=200 if job else 404)
            return self._send({"error": "not found"}, status=404)

    return Handler


def serve(host: str | None = None, port: int | None = None) -> int:
    _, settings, _, _ = build_context()
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