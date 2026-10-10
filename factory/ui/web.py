"""Веб-дашборд фабрики (стандартный http.server, без зависимостей).

Интерфейс локализован (русский по умолчанию / English / 中文).
Язык: настройка ui.lang (меняется в дашборде или `factory settings set ui.lang en`),
либо переменная окружения FACTORY_LANG.

Эндпоинты:
  GET  /                          — дашборд
  GET  /api/settings              — настройки
  GET  /api/settings/set?key=&value=
  GET  /api/domains               — домены
  GET  /api/domains/toggle?name=&enabled=
  GET  /api/ledger                — журнал
  GET  /api/quotas                — квоты исполнителей Lab100 (этап 4)
  GET  /api/inventory             — карта «что умеем» (этап 0)
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
from ..i18n import choose, make_translator
from ..jobs import JobManager
from ..paths import repo_root

_TEMPLATE = """<!doctype html>
<html lang="{{lang}}"><head><meta charset="utf-8">
<title>{{t_title}}</title>
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
 select{width:auto}
</style></head><body>
<header><h1>{{t_title}}</h1></header>
<main>
 <section><h2>{{t_new_task}}</h2>
  <textarea id="goal" rows="3" placeholder="{{t_goal_ph}}"></textarea>
  <label>{{t_domain}} <input id="domain" placeholder="auto"></label>
  <label>{{t_mode}}
   <select id="automation">
    <option value="">{{t_from_settings}}</option><option value="auto">auto</option><option value="semi">semi</option>
   </select></label>
  <label><input type="checkbox" id="dry"> dry-run</label><br>
  <button onclick="submitTask()">{{t_run}}</button>
  <div id="submitMsg"></div>
 </section>
 <section><h2>{{t_jobs}}</h2><div id="jobs"></div></section>
 <section><h2>{{t_settings}}</h2><div id="settings"></div></section>
 <section><h2>{{t_dynamic_domains}}</h2><div id="domains"></div></section>
 <section><h2>{{t_ledger}}</h2><div id="ledger"></div></section>
 <section><h2>{{t_quotas}}</h2><div id="quotas"></div></section>
 <section><h2>{{t_monitoring}}</h2><div id="monitor"></div></section>
</main>
<script>
const T={
 req:'{{t_goal_required}}',start:'{{t_task_started}}',err:'{{t_error}}',
 thStatus:'{{t_th_status}}',thGoal:'{{t_th_goal}}',thDomain:'{{t_th_domain}}',
 thProvider:'{{t_th_provider}}',thWindow:'{{t_th_window}}',thLimit:'{{t_th_limit}}',
 thUsed:'{{t_th_used}}',thRemaining:'{{t_th_remaining}}',noData:'{{t_no_data}}',
 invSummary:'{{t_inventory_summary}}',
 appr:'{{t_btn_approve}}',skip:'{{t_btn_skip}}',
 profile:'{{t_profile}}',automation:'{{t_automation}}',current:'{{t_current}}',
 uiLabel:'{{t_ui_label}}',thName:'{{t_th_name}}',thPriority:'{{t_th_priority}}',
 thCaps:'{{t_th_caps}}',thState:'{{t_th_state}}',on:'{{t_on}}',off:'{{t_off}}'
};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function j(u){const r=await fetch(u);return r.json()}
async function toggle(name,enabled){await fetch(`/api/domains/toggle?name=${encodeURIComponent(name)}&enabled=${enabled}`);load()}
async function setSetting(k,v){await fetch(`/api/settings/set?key=${encodeURIComponent(k)}&value=${encodeURIComponent(v)}`);load()}
async function setLang(v){await fetch(`/api/settings/set?key=ui.lang&value=${encodeURIComponent(v)}`);setTimeout(()=>location.reload(),150)}
async function submitTask(){
 const goal=document.getElementById('goal').value.trim();
 if(!goal){document.getElementById('submitMsg').textContent=T.req;return}
 const body={
  schema:'invr.task/1',
  goal,
  domain:document.getElementById('domain').value.trim()||null,
  automation:document.getElementById('automation').value||null,
  dry_run:document.getElementById('dry').checked
 };
 const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const d=await r.json();
 document.getElementById('submitMsg').textContent=d.ok
   ?T.start.replace('{id}',d.job.id).replace('{status}',d.job.status)
   :T.err+(d.error||'');
 loadJobs();
}
async function approveJob(id,ok){
 await fetch('/api/jobs/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,ok})});
 loadJobs();
}
async function loadJobs(){
 const r=await j('/api/jobs');
 document.getElementById('jobs').innerHTML='<table><tr><th>id</th><th>'+T.thStatus+'</th><th>'+T.thGoal+'</th><th>'+T.thDomain+'</th><th></th></tr>'+
  r.jobs.map(x=>{
   let actions='';
   if(x.awaiting) actions=`<button onclick="approveJob('${x.id}',true)">${T.appr}</button> <button class="warn" onclick="approveJob('${x.id}',false)">${T.skip}</button>`;
   const cls=x.status==='awaiting-approval'?'wait':(x.status==='done'?'on':(x.status==='running'?'wait':'off'));
   return `<tr><td>${x.id}</td><td class="${cls}">${x.status}</td><td>${esc(x.goal).slice(0,60)}</td><td>${x.domain||'auto'}</td><td>${actions}</td></tr>`;
  }).join('')+'</table>';
}
async function load(){
 const s=await j('/api/settings');
 document.getElementById('settings').innerHTML =
  `<p>${T.profile} <code>${esc(s.active_profile)}</code></p>
   <p>${T.automation}
     <button onclick="setSetting('automation.mode','auto')">auto</button>
     <button onclick="setSetting('automation.mode','semi')">semi</button>
     &nbsp;${T.current} <b>${esc(s.automation)}</b></p>
   <p>${T.uiLabel}
     <button onclick="setSetting('ui.mode','cli')">cli</button>
     <button onclick="setSetting('ui.mode','web')">web</button>
     <button onclick="setSetting('ui.mode','tray')">tray</button>
     <button onclick="setSetting('ui.mode','all')">all</button>
     &nbsp;${T.current} <b>${esc(s.ui)}</b></p>
   <p><label>{{t_lang}} <select id="langSel" onchange="setLang(this.value)">
     <option value="ru">Русский</option><option value="en">English</option><option value="zh">中文</option>
   </select></label></p>`;
 if(document.getElementById('langSel')) document.getElementById('langSel').value=(s.lang||'ru');
 const d=await j('/api/domains');
 document.getElementById('domains').innerHTML='<table><tr><th>'+T.thName+'</th><th>'+T.thPriority+'</th><th>'+T.thCaps+'</th><th>'+T.thState+'</th></tr>'+
  d.domains.map(x=>`<tr><td>${esc(x.name)}<br><small>${esc(x.title||'')}</small></td><td>${x.priority}</td>
   <td>${x.capabilities.map(esc).join(', ')}</td>
   <td class="${x.enabled?'on':'off'}">${x.enabled?T.on:T.off}
   <button onclick="toggle('${x.name}',${!x.enabled})">${x.enabled?T.off:T.on}</button></td></tr>`).join('')+'</table>';
 const l=await j('/api/ledger');
 document.getElementById('ledger').innerHTML='<pre>'+l.events.map(e=>esc(JSON.stringify(e))).join('\\n')+'</pre>';
}
async function loadQuotas(){
 const q=await j('/api/quotas');
 const box=document.getElementById('quotas');
 if(q.available===false){box.innerHTML='<p>'+esc(q.note||'')+'</p>';return}
 const rows=Object.entries(q.providers||{});
 if(!rows.length){box.innerHTML='<p>'+T.noData+'</p>';return}
 box.innerHTML='<table><tr><th>'+T.thProvider+'</th><th>'+T.thWindow+'</th><th>'+T.thLimit+'</th><th>'+T.thUsed+'</th><th>'+T.thRemaining+'</th></tr>'+
  rows.map(([p,r])=>`<tr><td>${esc(p)}</td><td>${esc(r.window)}</td><td>${r.limit==null?'∞':r.limit}</td><td>${r.used}</td><td>${r.remaining==null?'∞':r.remaining}${r.percent==null?'':' ('+r.percent+'%)'}</td></tr>`).join('')+'</table>';
}
async function loadMonitoring(){
 const inv=await j('/api/inventory');
 const s=(inv&&inv.summary)||{};
 document.getElementById('monitor').innerHTML='<p>'+esc(T.invSummary
  .replace('{present}',s.repos_present??0).replace('{total}',s.repos_total??0)
  .replace('{tools}',s.tools??0).replace('{providers}',s.providers??0))+'</p>';
}
load();loadJobs();loadQuotas();loadMonitoring();
setInterval(loadJobs,2000);
</script></body></html>"""


def _page(lang: str) -> str:
    tr = make_translator(lang=lang)
    tokens = {
        "lang": lang,
        "t_title": tr("web.title"),
        "t_new_task": tr("web.new_task"),
        "t_goal_ph": tr("web.goal_ph"),
        "t_domain": tr("web.domain"),
        "t_mode": tr("web.mode"),
        "t_from_settings": tr("web.from_settings"),
        "t_run": tr("web.run"),
        "t_jobs": tr("web.jobs"),
        "t_settings": tr("web.settings"),
        "t_dynamic_domains": tr("web.dynamic_domains"),
        "t_ledger": tr("web.ledger"),
        "t_goal_required": tr("web.goal_required"),
        "t_task_started": tr("web.task_started"),
        "t_error": tr("web.error"),
        "t_th_status": tr("web.th_status"),
        "t_th_goal": tr("web.th_goal"),
        "t_th_domain": tr("web.th_domain"),
        "t_btn_approve": tr("web.btn_approve"),
        "t_btn_skip": tr("web.btn_skip"),
        "t_profile": tr("web.profile"),
        "t_automation": tr("web.automation"),
        "t_current": tr("web.current"),
        "t_ui_label": tr("web.ui_label"),
        "t_th_name": tr("web.th_name"),
        "t_th_priority": tr("web.th_priority"),
        "t_th_caps": tr("web.th_caps"),
        "t_th_state": tr("web.th_state"),
        "t_on": tr("web.on"),
        "t_off": tr("web.off"),
        "t_lang": tr("web.lang"),
        "t_quotas": tr("web.quotas"),
        "t_monitoring": tr("web.monitoring"),
        "t_th_provider": tr("web.th_provider"),
        "t_th_window": tr("web.th_window"),
        "t_th_limit": tr("web.th_limit"),
        "t_th_used": tr("web.th_used"),
        "t_th_remaining": tr("web.th_remaining"),
        "t_inventory_summary": tr("web.inventory_summary"),
        "t_no_data": tr("web.no_data"),
    }
    html = _TEMPLATE
    for key, value in tokens.items():
        html = html.replace("{{" + key + "}}", value)
    return html


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
                return self._send(_page(choose(settings)).encode("utf-8"),
                                  content_type="text/html; charset=utf-8")
            if path == "/api/settings":
                return self._send({
                    "active_profile": settings.get("active_profile", "default"),
                    "automation": settings.automation,
                    "ui": settings.ui,
                    "lang": settings.lang,
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
            if path == "/api/quotas":
                from .. import quotas
                data = quotas.build(settings)
                if not data.get("available"):
                    data["note"] = make_translator(settings)("quotas.not_available")
                return self._send(data)
            if path == "/api/inventory":
                from .. import inventory
                return self._send(inventory.load_saved(settings) or inventory.build(settings))
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
    tr = make_translator(settings)
    host = host or str(settings.get("ui.web.host", "127.0.0.1"))
    port = port or int(settings.get("ui.web.port", 8787))
    server = ThreadingHTTPServer((host, port), _handler_class())
    print(tr("web.serving", host=host, port=port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + tr("web.stopped"))
    finally:
        server.server_close()
    return 0