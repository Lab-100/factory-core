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
  GET  /api/catalog               — каталог артефактов по доменам (§5.6)
  GET  /api/catalog/show?path=    — отчёт записи каталога (report.md)
  GET  /api/doctor                — здоровье адаптеров и интеграций
  GET  /api/arsenal               — арсенал: элементы и состояния узлов (пульт)
  GET  /api/logs?source=&element= — журналы арсенала (по элементу и сводно)
  GET  /api/jobs                  — очередь задач
  GET  /api/jobs/get?id=
  GET  /api/sessions              — список сессий (история запросов)
  GET  /api/sessions/get?id=      — сессия целиком (сообщения)
  GET  /api/export?kind=&id=&format= — экспорт заказа/сессии (md|json|zip)
  POST /api/run                   — запустить задачу (JSON invr.task/1)
  POST /api/jobs/approve          — решение по шагу: {"id": ..., "ok": true|false}
  POST /api/sessions              — создать сессию: {"title": ...}
  POST /api/sessions/append       — добавить сообщение: {"id":, "role":, "text": ...}
  POST /api/sessions/delete       — удалить сессию: {"id": ...}
  POST /api/upload                — загрузка вложений (multipart/form-data)
  POST /api/supervise/action      — действие над службой: {"element": ..., "action": "up|down|restart"}
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
from ..sessions import SessionStore


def _extract_boundary(content_type: str) -> str:
    for part in str(content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            return part.split("=", 1)[1].strip('"')
    return ""


def _parse_multipart(body: bytes, content_type: str) -> tuple[str, list[tuple[str, bytes]]]:
    """Разбор multipart/form-data: вернуть (scope, [(имя файла, данные)])."""
    boundary = _extract_boundary(content_type)
    if not boundary:
        return "default", []
    delimiter = b"--" + boundary.encode("utf-8")
    scope = "default"
    files: list[tuple[str, bytes]] = []
    for chunk in body.split(delimiter):
        if not chunk or chunk in (b"--\r\n", b"--", b"\r\n"):
            continue
        head, sep, content = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        content = content.rstrip(b"\r\n")
        if content.endswith(b"--"):
            content = content[:-2].rstrip(b"\r\n")
        headers = head.decode("utf-8", "replace")
        field = filename = None
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        field = token.split("=", 1)[1].strip('"')
                    elif token.startswith("filename="):
                        filename = token.split("=", 1)[1].strip('"')
        if filename:
            files.append((filename, content))
        elif field == "scope":
            scope = content.decode("utf-8", "replace") or "default"
    return scope, files

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
  <label>{{t_attach}} <input type="file" id="files" multiple style="width:auto"></label><br>
  <button onclick="submitTask()">{{t_run}}</button>
  <div id="submitMsg"></div>
 </section>
 <section><h2>{{t_sessions}}</h2>
  <div style="margin-bottom:8px">
   <input id="sesTitle" placeholder="{{t_ses_title_ph}}" style="width:55%">
   <button onclick="newSession()">{{t_ses_new}}</button>
  </div>
  <div id="sessions"></div>
  <div id="sessionView"></div>
 </section>
 <section><h2>{{t_jobs}}</h2><div id="jobs"></div></section>
 <section><h2>{{t_settings}}</h2><div id="settings"></div></section>
 <section><h2>{{t_project}}</h2><div id="project"></div></section>
 <section><h2>{{t_providers}}</h2><div id="providers"></div></section>
 <section><h2>{{t_dynamic_domains}}</h2><div id="domains"></div></section>
 <section><h2>{{t_ledger}}</h2><div id="ledger"></div></section>
 <section><h2>{{t_quotas}}</h2><div id="quotas"></div></section>
 <section><h2>{{t_monitoring}}</h2><div id="monitor"></div></section>
 <section><h2>{{t_catalog}}</h2><div id="catalog"></div></section>
 <section><h2>{{t_doctor}}</h2><div id="doctor"></div></section>
 <section><h2>{{t_arsenal}}</h2><div id="arsenal"></div></section>
 <section><h2>{{t_logs}}</h2><div id="logs"></div></section>
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
 thCaps:'{{t_th_caps}}',thState:'{{t_th_state}}',on:'{{t_on}}',off:'{{t_off}}',
 thUpdated:'{{t_th_updated}}',catalogEmpty:'{{t_catalog_empty}}',doctorSummary:'{{t_doctor_summary}}',
 thClass:'{{t_web_th_class}}',thOwner:'{{t_web_th_owner}}',thDetail:'{{t_web_th_detail}}',
 arsenalSummary:'{{t_web_arsenal_summary}}',logsAll:'{{t_web_logs_all}}',logsPh:'{{t_web_logs_ph}}',
 refresh:'{{t_web_refresh}}',
 projNone:'{{t_proj_none}}',
 provEnabled:'{{t_th_enabled}}',provAvail:'{{t_avail}}',provOn:'{{t_turn_on}}',provOff:'{{t_turn_off}}',provHint:'{{t_prov_hint}}',
 thTitle:'{{t_ses_th_title}}',thCount:'{{t_ses_th_count}}',
 sesNew:'{{t_ses_new}}',sesOpen:'{{t_ses_open}}',sesSend:'{{t_ses_send}}',sesPh:'{{t_ses_ph}}',sesDel:'{{t_ses_delete}}',
 roleUser:'{{t_role_user}}',roleAssistant:'{{t_role_assistant}}',roleSystem:'{{t_role_system}}'
};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function j(u){const r=await fetch(u);return r.json()}
async function toggle(name,enabled){await fetch(`/api/domains/toggle?name=${encodeURIComponent(name)}&enabled=${enabled}`);load()}
async function setSetting(k,v){await fetch(`/api/settings/set?key=${encodeURIComponent(k)}&value=${encodeURIComponent(v)}`);load()}
async function setLang(v){await fetch(`/api/settings/set?key=ui.lang&value=${encodeURIComponent(v)}`);setTimeout(()=>location.reload(),150)}
async function submitTask(){
 const goal=document.getElementById('goal').value.trim();
 if(!goal){document.getElementById('submitMsg').textContent=T.req;return}
 const fi=document.getElementById('files');
 let attachments=[];
 if(fi&&fi.files.length){
   const fd=new FormData();
   fd.append('scope', currentSession||'default');
   for(const f of fi.files) fd.append('file', f);
   const up=await fetch('/api/upload',{method:'POST',body:fd});
   const ud=await up.json();
   if(!ud.ok){document.getElementById('submitMsg').textContent=T.err+(ud.error||'');return}
   attachments=(ud.attachments||[]).map(a=>a.path);
   fi.value='';
 }
 const body={
  schema:'invr.task/1',
  goal,
  domain:document.getElementById('domain').value.trim()||null,
  automation:document.getElementById('automation').value||null,
  dry_run:document.getElementById('dry').checked
 };
 if(attachments.length) body.inputs={attachments};
 if(currentSession) body.session=currentSession;
 const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const d=await r.json();
 document.getElementById('submitMsg').textContent=d.ok
   ?T.start.replace('{id}',d.job.id).replace('{status}',d.job.status)
   :T.err+(d.error||'');
 loadJobs();
 if(currentSession) openSession(currentSession);
}
let currentSession='';
function roleName(r){return r==='user'?T.roleUser:(r==='assistant'?T.roleAssistant:T.roleSystem)}
async function newSession(){
 const title=document.getElementById('sesTitle').value.trim();
 const r=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
 const d=await r.json();
 document.getElementById('sesTitle').value='';
 if(d.ok){currentSession=d.session.id;loadSessions();openSession(d.session.id)}
}
async function loadSessions(){
 const r=await j('/api/sessions');
 const items=r.sessions||[];
 document.getElementById('sessions').innerHTML=
  '<table><tr><th>id</th><th>'+T.thTitle+'</th><th>'+T.thCount+'</th><th>'+T.thUpdated+'</th><th></th></tr>'+
  items.map(s=>{
   const openBtn='<button onclick="openSession(\''+s.id+'\')">'+T.sesOpen+'</button>';
   const delBtn='<button class="warn" onclick="delSession(\''+s.id+'\')">'+T.sesDel+'</button>';
   return '<tr><td><code>'+esc(s.id)+'</code></td><td>'+esc(s.title)+'</td><td>'+s.count+'</td>'+
    '<td><small>'+esc((s.updated||'').slice(0,19))+'</small></td><td>'+openBtn+' '+delBtn+'</td></tr>';
  }).join('')+'</table>';
}
async function openSession(id){
 currentSession=id;
 const s=await j('/api/sessions/get?id='+encodeURIComponent(id));
 if(s.error){document.getElementById('sessionView').innerHTML='';return}
 const rows=(s.messages||[]).map(m=>'<div><b>'+esc(roleName(m.role))+'</b>: '+esc(m.text)+'</div>').join('')||T.noData;
 document.getElementById('sessionView').innerHTML=
  '<h3>'+esc(s.title)+' <code>'+esc(s.id)+'</code></h3>'+
  '<div style="max-height:260px;overflow:auto;border:1px solid #262b36;border-radius:8px;padding:8px;margin-bottom:8px">'+rows+'</div>'+
  '<textarea id="sesMsg" rows="2" placeholder="'+esc(T.sesPh)+'"></textarea>'+
  '<button onclick="sendMsg()">'+T.sesSend+'</button> '+
  '<button onclick="window.open(\'/api/export?kind=session&id='+s.id+'&format=md\')">md</button> '+
  '<button onclick="window.open(\'/api/export?kind=session&id='+s.id+'&format=json\')">json</button> '+
  '<button class="warn" onclick="delSession(\''+s.id+'\')">'+T.sesDel+'</button>';
}
async function sendMsg(){
 const ta=document.getElementById('sesMsg');
 const text=ta?ta.value.trim():'';
 if(!currentSession||!text) return;
 await fetch('/api/sessions/append',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:currentSession,role:'user',text})});
 openSession(currentSession);
}
async function delSession(id){
 await fetch('/api/sessions/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
 if(currentSession===id){currentSession='';document.getElementById('sessionView').innerHTML=''}
 loadSessions();
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
   const exp=`<button onclick="window.open('/api/export?kind=job&id=${x.id}&format=md')">md</button>`+
    `<button onclick="window.open('/api/export?kind=job&id=${x.id}&format=json')">json</button>`+
    `<button onclick="window.open('/api/export?kind=job&id=${x.id}&format=zip')">zip</button>`;
   actions=(actions?actions+' ':'')+exp;
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
async function loadProject(){
 const p=await j('/api/project');
 const box=document.getElementById('project');
 box.innerHTML= p.path
  ? '<p><code>'+esc(p.path)+'</code></p><pre>'+esc(JSON.stringify(p.data,null,2))+'</pre>'
  : '<p>'+esc(T.projNone)+'</p>';
}
async function loadProviders(){
 const r=await j('/api/providers');
 const items=r.providers||[];
 const box=document.getElementById('providers');
 if(!items.length){box.innerHTML='<p>'+T.noData+'</p>';return}
 box.innerHTML='<table><tr><th>'+T.thName+'</th><th>'+T.thTitle+'</th><th>'+T.provAvail+'</th><th>'+T.provEnabled+'</th><th></th></tr>'+
  items.map(p=>{
   const cmd=p.command?'<br><small><code>'+esc(p.command)+'</code></small>':'';
   const btn=p.enabled
    ?'<button class="warn" onclick="toggleProvider(\''+esc(p.name)+'\',false)">'+T.provOff+'</button>'
    :'<button onclick="toggleProvider(\''+esc(p.name)+'\',true)">'+T.provOn+'</button>';
   return '<tr><td><code>'+esc(p.name)+'</code></td><td>'+esc(p.title)+cmd+'</td><td class="'+(p.available?'on':'off')+'">'+(p.available?T.on:T.off)+'</td><td class="'+(p.enabled?'on':'off')+'">'+(p.enabled?T.on:T.off)+'</td><td>'+btn+'</td></tr>';
  }).join('')+'</table><p><small>'+esc(T.provHint)+'</small></p>';
}
async function toggleProvider(name,enabled){
 await fetch('/api/providers/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,enabled})});
 loadProviders();
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
async function loadCatalog(){
 const c=await j('/api/catalog');
 const box=document.getElementById('catalog');
 const items=c.items||[];
 if(!items.length){box.innerHTML='<p>'+esc(T.catalogEmpty)+'</p>';return}
 box.innerHTML='<table><tr><th>'+T.thDomain+'</th><th>'+T.thGoal+'</th><th>'+T.thUpdated+'</th><th>'+T.thState+'</th></tr>'+
  items.map(x=>`<tr><td>${esc(x.domain||'-')}/<code>${esc(x.name)}</code></td><td>${esc(x.goal).slice(0,60)}</td>
   <td><small>${esc((x.updated||'').slice(0,19))}</small></td>
   <td class="${x.ok?'on':'off'}">${x.ok?T.on:T.off}</td></tr>`).join('')+'</table>';
}
async function loadDoctor(){
 const d=await j('/api/doctor');
 const adpts=d.adapters||[], ints=d.integrations||[];
 const ready=adpts.filter(x=>x.available).length;
 const readyInt=ints.filter(x=>x.available).length;
 const rows=adpts.map(x=>[x.adapter,x.available]).concat(ints.map(x=>[x.integration,x.available]));
 document.getElementById('doctor').innerHTML=
  '<p>'+esc(T.doctorSummary
   .replace('{ready}',ready).replace('{total}',adpts.length)
   .replace('{iready}',readyInt).replace('{itotal}',ints.length))+'</p>'+
  '<table><tr><th>'+T.thName+'</th><th>'+T.thState+'</th></tr>'+
  rows.map(([n,a])=>`<tr><td>${esc(n)}</td><td class="${a?'on':'off'}">${a?T.on:T.off}</td></tr>`).join('')+'</table>';
}
async function loadArsenal(){
 const a=await j('/api/arsenal');
 const s=a.summary||{};
 const cls={active:'on','need-guard':'wait',fault:'off'};
 const rows=(a.nodes||[]).map(n=>{
  const canAct=n.class==='service'&&n.meta&&n.meta.start&&n.meta.start.length;
  const ctl=canAct?`<button onclick="act('${n.id}','restart')">↻</button>`:'';
  return `<tr><td><code>${esc(n.name)}</code></td><td>${esc(n.class)}</td>`+
   `<td class="${cls[n.state]||''}">${esc(n.state)}</td><td>${esc(n.owner)}</td>`+
   `<td><small>${esc(n.detail||'')}</small></td><td>${ctl}</td></tr>`;
 }).join('');
 document.getElementById('arsenal').innerHTML=
  '<p>'+esc(T.arsenalSummary.replace('{working}',s.working??0).replace('{total}',s.total??0)
    .replace('{attention}',s.attention??0).replace('{mode}',a.mode||''))+'</p>'+
  '<table><tr><th>'+T.thName+'</th><th>'+T.thClass+'</th><th>'+T.thState+'</th><th>'+T.thOwner+'</th><th>'+T.thDetail+'</th><th></th></tr>'+rows+'</table>';
}
async function act(id,action){
 await fetch('/api/supervise/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({element:id,action})});
 loadArsenal();loadLogs();
}
async function loadLogs(){
 const sel=document.getElementById('logSrc');
 const src=sel?sel.value:'';
 const inp=document.getElementById('logEl');
 const el=inp?inp.value:'';
 const r=await j(`/api/logs?limit=200${src?('&source='+encodeURIComponent(src)):''}${el?('&element='+encodeURIComponent(el)):''}`);
 const opts=(r.sources||[]).map(x=>`<option value="${esc(x.id)}"${src===x.id?' selected':''}>${esc(x.name)}${x.available?'':' (—)'}</option>`).join('');
 document.getElementById('logs').innerHTML=
  '<p><select id="logSrc" onchange="loadLogs()"><option value="">'+T.logsAll+'</option>'+opts+'</select> '+
  '<input id="logEl" style="width:auto" placeholder="'+T.logsPh+'" value="'+esc(el)+'"> '+
  '<button onclick="loadLogs()">'+T.refresh+'</button></p>'+
  '<pre style="max-height:320px;overflow:auto">'+r.lines.map(x=>esc('['+x.source+'] '+x.line)).join('\\n')+'</pre>';
}
load();loadJobs();loadSessions();loadProject();loadProviders();loadQuotas();loadMonitoring();loadCatalog();loadDoctor();loadArsenal();loadLogs();
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
        "t_catalog": tr("web.catalog"),
        "t_catalog_empty": tr("web.catalog_empty"),
        "t_doctor": tr("web.doctor"),
        "t_doctor_summary": tr("web.doctor_summary"),
        "t_th_updated": tr("web.th_updated"),
        "t_arsenal": tr("web.arsenal"),
        "t_logs": tr("web.logs"),
        "t_web_th_class": tr("web.th_class"),
        "t_web_th_owner": tr("web.th_owner"),
        "t_web_th_detail": tr("web.th_detail"),
        "t_web_arsenal_summary": tr("web.arsenal_summary"),
        "t_web_logs_all": tr("web.logs_all"),
        "t_web_logs_ph": tr("web.logs_ph"),
        "t_web_refresh": tr("web.refresh"),
        "t_sessions": tr("web.sessions"),
        "t_attach": tr("web.attach"),
        "t_project": tr("web.project"),
        "t_proj_none": tr("web.proj_none"),
        "t_ses_new": tr("web.ses_new"),
        "t_ses_title_ph": tr("web.ses_title_ph"),
        "t_ses_open": tr("web.ses_open"),
        "t_ses_send": tr("web.ses_send"),
        "t_ses_ph": tr("web.ses_ph"),
        "t_ses_delete": tr("web.ses_delete"),
        "t_ses_th_title": tr("web.ses_th_title"),
        "t_ses_th_count": tr("web.ses_th_count"),
        "t_role_user": tr("web.role_user"),
        "t_role_assistant": tr("web.role_assistant"),
        "t_role_system": tr("web.role_system"),
        "t_providers": tr("web.providers"),
        "t_th_enabled": tr("web.th_enabled"),
        "t_avail": tr("web.avail"),
        "t_turn_on": tr("web.turn_on"),
        "t_turn_off": tr("web.turn_off"),
        "t_prov_hint": tr("web.prov_hint"),
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
        def _send(self, payload, status=200, content_type="application/json", filename=None):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # тише
            return

        def do_GET(self):  # noqa: N802
            root, settings, registry, ledger = build_context()
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
            if path == "/api/project":
                from .. import projects
                project_path = settings.project_path
                return self._send({
                    "path": str(project_path) if project_path else None,
                    "data": projects.load_project(project_path) if project_path else {},
                })
            if path == "/api/providers":
                from .. import providers
                return self._send({
                    "providers": providers.discover(settings),
                    "auto_route": bool(settings.get("providers.auto_route", False)),
                })
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
            if path == "/api/catalog":
                from .. import catalog
                return self._send(catalog.build(settings))
            if path == "/api/catalog/show":
                from .. import catalog
                text = catalog.report(settings, query.get("path", [""])[0])
                return self._send({"path": query.get("path", [""])[0], "content": text},
                                  status=200 if text is not None else 404)
            if path == "/api/doctor":
                from ..doctor import run_doctor
                return self._send(run_doctor(settings))
            if path == "/api/arsenal":
                from ..supervisor import Supervisor
                sup = Supervisor(settings, root=root, ledger=ledger)
                return self._send(sup.snapshot())
            if path == "/api/logs":
                from .. import logs as logmod
                default_limit = int(settings.get("logs.limit_default", 200))
                try:
                    limit = int(query.get("limit", [str(default_limit)])[0] or default_limit)
                except ValueError:
                    limit = default_limit
                return self._send(logmod.tail(
                    settings,
                    source=(query.get("source", [""])[0] or None),
                    limit=limit,
                    element=(query.get("element", [""])[0] or None),
                    level=(query.get("level", [""])[0] or None),
                ))
            if path == "/api/jobs":
                return self._send({"jobs": [_jobs().summary(j) for j in _jobs().list()]})
            if path == "/api/jobs/get":
                job = _jobs().get(query.get("id", [""])[0])
                return self._send(job or {"error": "not found"}, status=200 if job else 404)
            if path == "/api/sessions":
                return self._send({"sessions": SessionStore(root).list()})
            if path == "/api/sessions/get":
                session = SessionStore(root).get(query.get("id", [""])[0])
                return self._send(session or {"error": "not found"},
                                  status=200 if session else 404)
            if path == "/api/export":
                from .. import export as exp
                result = exp.export(root, query.get("kind", ["job"])[0],
                                    query.get("id", [""])[0],
                                    query.get("format", ["md"])[0])
                if result is None:
                    return self._send({"error": "not found"}, status=404)
                data, ctype, fname = result
                return self._send(data, content_type=ctype, filename=fname)
            return self._send({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            path = parsed.path
            content_type = self.headers.get("Content-Type") or ""
            if path == "/api/upload" and content_type.startswith("multipart/form-data"):
                from ..attachments import AttachmentsStore
                scope, files = _parse_multipart(raw, content_type)
                store = AttachmentsStore(repo_root())
                saved = []
                for name, blob in files:
                    try:
                        saved.append(store.save(scope, name, blob))
                    except ValueError as exc:
                        return self._send({"ok": False, "error": str(exc)}, status=400)
                return self._send({"ok": True, "scope": scope, "attachments": saved})
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._send({"ok": False, "error": "bad json"}, status=400)
            if path == "/api/run":
                job = _jobs().submit(data)
                return self._send({"ok": True, "job": _jobs().summary(job)})
            if path == "/api/jobs/approve":
                job = _jobs().approve(data.get("id"), ok=bool(data.get("ok", True)))
                return self._send({
                    "ok": job is not None,
                    "job": _jobs().summary(job) if job else None,
                }, status=200 if job else 404)
            if path == "/api/sessions":
                session = SessionStore(repo_root()).create(
                    title=str(data.get("title") or ""),
                    project=str(data.get("project") or ""))
                return self._send({"ok": True, "session": session})
            if path == "/api/sessions/append":
                session = SessionStore(repo_root()).append(
                    str(data.get("id") or ""),
                    str(data.get("role") or "user"),
                    str(data.get("text") or ""))
                if session is not None:
                    return self._send({"ok": True, "session": session})
                return self._send({"ok": False, "error": "not found"}, status=404)
            if path == "/api/sessions/delete":
                ok = SessionStore(repo_root()).delete(str(data.get("id") or ""))
                return self._send({"ok": ok}, status=200 if ok else 404)
            if path == "/api/providers/toggle":
                from .. import providers
                name = str(data.get("name") or "")
                ok = providers.set_enabled(settings, name,
                                           bool(data.get("enabled", True)))
                return self._send({"ok": ok}, status=200 if ok else 404)
            if path == "/api/supervise/action":
                from ..supervisor import Supervisor
                root, settings, _, ledger = build_context()
                sup = Supervisor(settings, root=root, ledger=ledger)
                result = sup.action(str(data.get("element") or ""),
                                    str(data.get("action") or "up"))
                return self._send(result, status=200 if result.get("ok") else 400)
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
        from ..single import record_instance

        record_instance(f"http://{host}:{port}/")
    except Exception:  # noqa: BLE001 — адрес для повторного запуска не критичен
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + tr("web.stopped"))
    finally:
        server.server_close()
    return 0