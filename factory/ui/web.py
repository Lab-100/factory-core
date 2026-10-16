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
<base target="_self">
<title>{{t_title}}</title>
<style>
 :root{--fs:14px}
 html{font-size:var(--fs)}
 *{box-sizing:border-box}
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6;font-size:1rem;line-height:1.5}
 header{position:sticky;top:0;z-index:40;padding:14px 24px 12px;background:#161a22;border-bottom:1px solid #262b36}
 h1{margin:0 0 4px;font-size:1.5rem;line-height:1.25}
 main{padding:24px;display:grid;gap:20px;max-width:1240px;margin:0 auto;grid-template-columns:minmax(0,1fr)}
 section{background:#161a22;border:1px solid #262b36;border-radius:12px;padding:18px 20px;min-width:0;scroll-margin-top:150px}
 section h2{font-size:1.12rem;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #262b36}
 table{width:100%;border-collapse:collapse;table-layout:auto}
 .tblwrap{overflow-x:auto;max-width:100%}
 thead th{background:#12161e;color:#b9c2d4;font-size:0.93rem}
 th{position:relative;user-select:none}
 th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #262b36;vertical-align:top}
 tbody tr:nth-child(2n){background:#131826}
 td{overflow-wrap:anywhere}
 pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;margin:8px 0;background:#0b0e13;border:1px solid #262b36;border-radius:8px;padding:10px 12px;font-size:13px;line-height:1.45;color:#cfd6e4;overflow-x:auto}
 .logctl{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 8px}
 th .rz{position:absolute;top:0;right:-4px;width:9px;height:100%;cursor:col-resize;z-index:2}
 th .rz:hover{background:#2d6cdf66}
 .on{color:#5ddc7d}.off{color:#e06c75}.wait{color:#e5c07b}
 button{background:#2d6cdf;border:0;color:#fff;min-height:32px;padding:6px 14px;border-radius:8px;cursor:pointer;margin:2px;font-size:0.95rem;font-family:inherit;transition:filter .15s}
 button:hover{filter:brightness(1.15)}
 button:active{filter:brightness(0.9)}
 button.warn{background:#c0392b}
 textarea,input,select{background:#0f1115;color:#e6e6e6;border:1px solid #3a4250;border-radius:8px;padding:8px 10px;width:100%;min-height:34px;font-size:inherit;font-family:inherit}
 textarea:focus-visible,input:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid #4d8dff;outline-offset:1px}
 textarea{resize:vertical}
 label{display:inline-block;margin:8px 14px 0 0}
 code{color:#9ad1ff;overflow-wrap:anywhere}
 select{width:auto}
 nav{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
 nav a{color:#9ad1ff;text-decoration:none;font-size:13px;background:#0f1115;border:1px solid #262b36;border-radius:8px;padding:6px 11px;min-height:28px}
 nav a:hover{border-color:#2d6cdf}
 nav a.on{border-color:#2d6cdf;color:#fff;background:#1b2130}
 section.collapsed > *:not(h2){display:none}
 h2{cursor:pointer;user-select:none}
 h2:hover{color:#9ad1ff}
 h2 .fold{color:#6b7280;font-size:12px;font-weight:400;margin-left:8px}
 tr.clickable:hover{background:#1b2130;cursor:pointer}
 .chip{display:inline-block;border-radius:10px;padding:2px 9px;font-size:12px;border:1px solid #262b36}
 .chip.active{color:#5ddc7d;border-color:#2f6b41}
 .chip.idle{color:#9ad1ff;border-color:#2d4a6b}
 .chip.unloaded{color:#8b93a7;border-color:#333a49}
 .chip.need-guard{color:#e5c07b;border-color:#6b5a2f}
 .chip.fault{color:#e06c75;border-color:#6b2f34}
 .infra-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:10px 0}
 .infra-col{min-width:0}
 .infra-col h4{margin:0 0 6px;font-size:13px;color:#9ad1ff}
 .node{border:1px solid #262b36;border-radius:8px;padding:8px 10px;margin-bottom:6px;cursor:pointer;background:#0f1115}
 .node:hover{border-color:#2d6cdf}
 .node small{color:#8b93a7}
 details summary{cursor:pointer;color:#9ad1ff;padding:5px 0;font-weight:500}
 img.shot{max-width:100%;border:1px solid #262b36;border-radius:8px;margin-top:8px}
 .hint{color:#8b93a7;font-size:13px;line-height:1.4}
 a.btn{display:inline-block;background:#2d6cdf;color:#fff;padding:7px 13px;border-radius:8px;text-decoration:none;font-size:13px;margin:2px}
 a.btn:hover{filter:brightness(1.15)}
 .tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
 .tab{background:#0f1115;color:#9ad1ff;border:1px solid #262b36;border-radius:8px;padding:7px 14px;cursor:pointer;font-size:13px;min-height:32px;font-family:inherit}
 .tab:hover{border-color:#2d6cdf}
 .tab.on{background:#2d6cdf;color:#fff;border-color:#2d6cdf}
 .pane{display:none}.pane.on{display:block}
 .fsrow{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}
 .fsrow input[type=range]{width:240px;min-height:auto;padding:0}
 .fsval{color:#9ad1ff;min-width:70px}
 .kvs{display:grid;grid-template-columns:minmax(130px,auto) 1fr;gap:6px 16px;font-size:14px}
 .kvs b{color:#9ad1ff;font-weight:500}
 @media (max-width:900px){
  header{padding:12px 14px 10px}
  main{padding:14px}
  section{padding:14px}
  .infra-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  label{display:block;margin:8px 0 0}
  .fsrow input[type=range]{width:100%}
 }
</style></head><body>
<header><h1>{{t_title}}</h1><nav id="nav"></nav></header>
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
 <section><h2>{{t_providers}}</h2><div id="providers"></div><p class="hint" id="provHint"></p></section>
 <section><h2>{{t_dynamic_domains}}</h2><div id="domains"></div></section>
 <section><h2>{{t_ledger}}</h2><div id="ledger"></div></section>
 <section><h2>{{t_quotas}}</h2><div id="quotas"></div></section>
 <section><h2>{{t_monitoring}}</h2><div id="monitor"></div></section>
 <section><h2>{{t_infra}}</h2><div id="infra"></div></section>
 <section><h2>{{t_catalog}}</h2><div id="catalog"></div></section>
 <section><h2>{{t_doctor}}</h2><div class="hint" id="doctorSum"></div><div id="doctor"></div></section>
 <section><h2>{{t_arsenal}}</h2><div class="hint" id="arsenalSum"></div><div id="arsenal"></div></section>
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
 nav:'{{t_web_nav}}',infraShot:'{{t_infra_shot}}',infraEmpty:'{{t_infra_empty}}',
 infraNodes:'{{t_infra_nodes}}',infraEvents:'{{t_infra_events}}',infraCand:'{{t_infra_candidates}}',
 infraNoCand:'{{t_infra_no_candidates}}',logsFiltered:'{{t_logs_filtered}}',ledgerEmpty:'{{t_ledger_empty}}',
 roleUser:'{{t_role_user}}',roleAssistant:'{{t_role_assistant}}',roleSystem:'{{t_role_system}}',
 lang:'{{t_lang}}',tabUi:'{{t_tab_ui}}',tabAuto:'{{t_tab_auto}}',tabNet:'{{t_tab_net}}',tabService:'{{t_tab_service}}',fontSize:'{{t_font_size}}',save:'{{t_save}}',
 tHost:'{{t_t_host}}',tPort:'{{t_t_port}}',tRefreshAll:'{{t_t_refresh_all}}',tServiceHint:'{{t_t_service_hint}}'
};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
/* --- окна: одно окно, новые вкладки не создаём --- */
window.open=function(){return null};
document.addEventListener('click',function(e){
 const a=e.target.closest&&e.target.closest('a');
 if(a&&(a.target==='_blank'||a.target==='_new')) e.preventDefault();
},true);
document.addEventListener('auxclick',function(e){if(e.button===1)e.preventDefault()},true);
/* --- размер шрифта --- */
const FS_KEY='factory.font_size';
function applyFontSize(px,save){document.documentElement.style.setProperty('--fs',px+'px');if(save!==false)localStorage.setItem(FS_KEY,String(px))}
function currentFontSize(){const v=parseInt(localStorage.getItem(FS_KEY)||'0',10);return v||14}
function restoreFontSize(){applyFontSize(currentFontSize(),false)}
/* --- точечное обновление: меняем только то, что изменилось --- */
function setTxt(el,v){if(!el)return;const s=String(v==null?'':v);if(el.textContent!==s)el.textContent=s}
function normCells(cells){return cells.map(c=>(typeof c==='string'?{h:c}:c))}
function ensureTable(box,heads,store){
 let tbl=box.querySelector('table[data-fx]');
 if(tbl&&tbl.dataset.cols===String(heads.length)){
  const th=tbl.querySelectorAll('thead th');
  heads.forEach((h,i)=>{if(th[i])setTxt(th[i],h)});
  return tbl;
 }
 box.innerHTML='<div class="tblwrap"><table data-fx="1" data-cols="'+heads.length+'"><colgroup>'+
  heads.map(()=>'<col>').join('')+'</colgroup><thead><tr>'+
  heads.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody></tbody></table></div>';
 box._fxHtml=null;
 tbl=box.querySelector('table');
 makeResizable(tbl,store);
 return tbl;
}
function patchRows(tbody,items,keyOf,cellsOf){
 const rows=tbody._rows||(tbody._rows=new Map());
 const alive=new Set();
 let anchor=tbody.firstElementChild;
 for(const item of items){
  const k=String(keyOf(item));
  let tr=rows.get(k);
  const cells=normCells(cellsOf(item));
  if(!tr){
   tr=document.createElement('tr');
   tr.innerHTML=cells.map(()=>'<td></td>').join('');
   rows.set(k,tr);
  }
  cells.forEach((c,i)=>{
   const td=tr.children[i];
   if(!td) return;
   const old=tr._norm&&tr._norm[i];
   if(!old||old.h!==c.h) td.innerHTML=c.h;
   if(c.c!=null&&td.className!==c.c) td.className=c.c;
  });
  tr._norm=cells;
  alive.add(k);
  if(anchor===tr) anchor=anchor.nextElementSibling;
  else tbody.insertBefore(tr,anchor);
 }
 for(const entry of rows) if(!alive.has(entry[0])){entry[1].remove();rows.delete(entry[0])}
}
function makeResizable(tbl,store){
 const cols=[...tbl.querySelectorAll('colgroup col')];
 const heads=[...tbl.querySelectorAll('thead th')];
 if(store){
  let saved=[];
  try{saved=JSON.parse(localStorage.getItem(store)||'[]')}catch(e){saved=[]}
  saved.forEach((w,i)=>{if(cols[i]&&w)cols[i].style.width=w+'px'});
  if(saved.length)tbl.style.tableLayout='fixed';
 }
 heads.forEach((th,i)=>{
  if(i>=cols.length-1||th.querySelector('.rz')) return;
  const handle=document.createElement('div');
  handle.className='rz';
  handle.title='тянуть — ширина столбца';
  handle.addEventListener('mousedown',e=>{
   e.preventDefault();e.stopPropagation();
   const x0=e.clientX,w0=cols[i].getBoundingClientRect().width;
   const move=ev=>{tbl.style.tableLayout='fixed';cols[i].style.width=Math.max(40,Math.round(w0+ev.clientX-x0))+'px'};
   const stop=()=>{
    document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',stop);
    localStorage.setItem(store,JSON.stringify(cols.map(c=>Math.round(c.getBoundingClientRect().width))));
   };
   document.addEventListener('mousemove',move);document.addEventListener('mouseup',stop);
  });
  th.appendChild(handle);
 });
}
function boxEmpty(box,html){box.innerHTML=html;box._fxHtml=null}
function setBlock(box,html){if(!box||box._fxHtml===html)return;box._fxHtml=html;box.innerHTML=html}
restoreFontSize();
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
 const box=document.getElementById('sessions');
 if(!items.length){boxEmpty(box,'<p>'+esc(T.noData)+'</p>');return}
 const tbl=ensureTable(box,['id',T.thTitle,T.thCount,T.thUpdated,''],'fx.sessions');
 patchRows(tbl.tBodies[0],items,s=>s.id,s=>[
  {h:'<code>'+esc(s.id)+'</code>'},
  {h:esc(s.title||'')},
  {h:String(s.count==null?0:s.count)},
  {h:'<small>'+esc((s.updated||'').slice(0,19))+'</small>'},
  {h:'<button onclick="openSession(&#39;'+esc(s.id)+'&#39;)">'+esc(T.sesOpen)+'</button> '+
    '<button class="warn" onclick="delSession(&#39;'+esc(s.id)+'&#39;)">'+esc(T.sesDel)+'</button>'}
 ]);
}

async function load(){
 const s=await j('/api/settings');
 if(!localStorage.getItem(FS_KEY)&&s.font_size) applyFontSize(parseInt(s.font_size,10)||14,false);
 const fs=currentFontSize();
 const box=document.getElementById('settings');
 setBlock(box,
  '<div class="tabs" id="setTabs">'+
   '<button class="tab" data-pane="pane-ui">'+esc(T.tabUi)+'</button>'+
   '<button class="tab" data-pane="pane-auto">'+esc(T.tabAuto)+'</button>'+
   '<button class="tab" data-pane="pane-net">'+esc(T.tabNet)+'</button>'+
   '<button class="tab" data-pane="pane-service">'+esc(T.tabService)+'</button>'+
  '</div>'+
  '<div class="pane" id="pane-ui">'+
   '<p class="fsrow">'+esc(T.fontSize)+
    '<input type="range" id="fsRange" min="11" max="26" step="1" value="'+fs+'" oninput="previewFont(this.value)">'+
    '<span class="fsval" id="fsVal">'+fs+' px</span>'+
    '<button onclick="saveFontSize()">'+esc(T.save)+'</button></p>'+
   '<p>'+esc(T.uiLabel)+
    ' <button onclick="setSetting(&#39;ui.mode&#39;,&#39;cli&#39;)">cli</button>'+
    ' <button onclick="setSetting(&#39;ui.mode&#39;,&#39;web&#39;)">web</button>'+
    ' <button onclick="setSetting(&#39;ui.mode&#39;,&#39;tray&#39;)">tray</button>'+
    ' <button onclick="setSetting(&#39;ui.mode&#39;,&#39;all&#39;)">all</button>'+
    ' &nbsp;'+esc(T.current)+' <b>'+esc(s.ui)+'</b></p>'+
   '<p><label>'+esc(T.lang)+' <select id="langSel" onchange="setLang(this.value)">'+
    '<option value="ru">Русский</option><option value="en">English</option><option value="zh">中文</option>'+
   '</select></label></p></div>'+
  '<div class="pane" id="pane-auto">'+
   '<p>'+esc(T.automation)+
    ' <button onclick="setSetting(&#39;automation.mode&#39;,&#39;auto&#39;)">auto</button>'+
    ' <button onclick="setSetting(&#39;automation.mode&#39;,&#39;semi&#39;)">semi</button>'+
    ' &nbsp;'+esc(T.current)+' <b>'+esc(s.automation)+'</b></p>'+
   '<div class="kvs"><b>'+esc(T.profile)+'</b><span><code>'+esc(s.active_profile)+'</code></span></div></div>'+
  '<div class="pane" id="pane-net">'+
   '<div class="kvs"><b>'+esc(T.tHost)+'</b><span><code>'+esc(s.web_host||'127.0.0.1')+'</code></span>'+
   '<b>'+esc(T.tPort)+'</b><span><code>'+esc(String(s.web_port||''))+'</code></span>'+
   '<b>'+esc(T.lang)+'</b><span>'+esc(s.lang||'ru')+'</span></div>'+
   '<p><button onclick="savePort()">'+esc(T.save)+'</button></p>'+
   '<p><label>'+esc(T.tPort)+' <input id="portInput" style="width:120px" value="'+esc(String(s.web_port||''))+'"></label></p></div>'+
  '<div class="pane" id="pane-service">'+
   '<p><button onclick="refreshAll()">'+esc(T.tRefreshAll)+'</button>'+
   ' <button onclick="showLogs(&#39;factory&#39;)">'+esc(T.logsPh||'Журналы')+'</button></p>'+
   '<p class="hint">'+esc(T.tServiceHint)+'</p></div>');
 const sel=document.getElementById('langSel');
 if(sel) sel.value=(s.lang||'ru');
 const range=document.getElementById('fsRange');
 if(range) range.value=fs;
 document.querySelectorAll('#setTabs .tab').forEach(tab=>{
  tab.addEventListener('click',()=>{
   document.querySelectorAll('#setTabs .tab').forEach(x=>x.classList.remove('on'));
   document.querySelectorAll('.pane').forEach(x=>x.classList.remove('on'));
   tab.classList.add('on');
   const pane=document.getElementById(tab.dataset.pane);
   if(pane) pane.classList.add('on');
   localStorage.setItem('factory.settab',tab.dataset.pane);
  });
 });
 const last=localStorage.getItem('factory.settab');
 const wanted=last?document.querySelector('#setTabs .tab[data-pane="'+last+'"]'):null;
 const first=document.querySelector('#setTabs .tab');
 const active=wanted||first;
 if(active) active.click();
 const d=await j('/api/domains');
 const boxD=document.getElementById('domains');
 const doms=d.domains||[];
 if(!doms.length){boxEmpty(boxD,'<p>'+esc(T.noData)+'</p>')}
 else{
  const tblD=ensureTable(boxD,[T.thName,T.thPriority,T.thCaps,T.thState],'fx.domains');
  patchRows(tblD.tBodies[0],doms,x=>x.name,x=>[
   {h:esc(x.name)+'<br><small>'+esc(x.title||'')+'</small>'},
   {h:String(x.priority)},
   {h:(x.capabilities||[]).map(esc).join(', ')},
   {h:(x.enabled?T.on:T.off)+' <button onclick="toggle(&#39;'+esc(x.name)+'&#39;,'+(!x.enabled)+')">'+
     (x.enabled?T.off:T.on)+'</button>',c:x.enabled?'on':'off'}
  ]);
 }
 const l=await j('/api/ledger');
 loadLedger(l);
 buildNav();
}
function previewFont(v){applyFontSize(parseInt(v,10)||14,false);setTxt(document.getElementById('fsVal'),v+' px')}
function saveFontSize(){
 const range=document.getElementById('fsRange');
 if(!range) return;
 const v=parseInt(range.value,10)||14;
 applyFontSize(v,true);
 fetch('/api/settings/set?key='+encodeURIComponent('ui.font_size')+'&value='+encodeURIComponent(v));
}
function savePort(){
 const input=document.getElementById('portInput');
 if(!input) return;
 setSetting('ui.web.port',input.value.trim());
}
function refreshAll(){
 load();loadJobs();loadSessions();loadProject();loadProviders();loadQuotas();
 loadMonitoring();loadInfra();loadCatalog();loadDoctor();loadArsenal();loadLogs();
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
  '<a class="btn" href="/api/export?kind=session&id='+s.id+'&format=md" download>md</a> '+
  '<a class="btn" href="/api/export?kind=session&id='+s.id+'&format=json" download>json</a> '+
  '<button class="warn" onclick="delSession(\\''+s.id+'\\')">'+T.sesDel+'</button>';
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
 const box=document.getElementById('jobs');
 const jobs=r.jobs||[];
 if(!jobs.length){boxEmpty(box,'<p>'+esc(T.noData)+'</p>');return}
 const tbl=ensureTable(box,['id',T.thStatus,T.thGoal,T.thDomain,''],'fx.jobs');
 patchRows(tbl.tBodies[0],jobs,x=>x.id,x=>{
  let actions='';
  if(x.awaiting) actions='<button onclick="approveJob(&#39;'+esc(x.id)+'&#39;,true)">'+esc(T.appr)+'</button> '+
   '<button class="warn" onclick="approveJob(&#39;'+esc(x.id)+'&#39;,false)">'+esc(T.skip)+'</button>';
  actions+='<a class="btn" href="/api/export?kind=job&id='+esc(x.id)+'&format=md" download>md</a>'+
   '<a class="btn" href="/api/export?kind=job&id='+esc(x.id)+'&format=json" download>json</a>'+
   '<a class="btn" href="/api/export?kind=job&id='+esc(x.id)+'&format=zip" download>zip</a>';
  const cls=x.status==='awaiting-approval'?'wait':(x.status==='done'?'on':(x.status==='running'?'wait':'off'));
  return [
   {h:esc(String(x.id))},
   {h:esc(String(x.status||'')),c:cls},
   {h:esc(String(x.goal||'')).slice(0,60)},
   {h:esc(String(x.domain||'auto'))},
   {h:actions}
  ];
 });
}
async function loadLedger(payload){
 const l=payload||await j('/api/ledger');
 const events=l.events||[];
 const box=document.getElementById('ledger');
 if(!events.length){setBlock(box,'<p class="hint">'+esc(T.ledgerEmpty)+'</p>');return}
 const rows=events.map(e=>{
  const ts=esc(String(e.ts||e.time||'').slice(0,19));
  const kind=esc(String(e.kind||e.event||e.action||'—'));
  const task=esc(String(e.task||e.goal||'').slice(0,48));
  const ok=e.ok===undefined?'':(e.ok?'<span class="on">ок</span>':'<span class="off">ошибка</span>');
  return '<tr class="clickable" onclick="toggleDump(this)">'+
   '<td><small>'+ts+'</small></td><td>'+kind+'</td><td>'+task+'</td><td>'+ok+'</td></tr>'+
   '<tr class="dump" style="display:none"><td colspan="4"><pre>'+esc(JSON.stringify(e))+'</pre></td></tr>';
 }).join('');
 setBlock(box,'<div class="tblwrap"><table><tr><th>'+T.thUpdated+'</th><th>'+T.thStatus+'</th><th>'+T.thGoal+'</th><th></th></tr>'+rows+'</table></div>');
}
function toggleDump(row){
 const next=row.nextElementSibling;
 if(next&&next.classList.contains('dump')) next.style.display=next.style.display==='table-row'?'none':'table-row';
}
function buildNav(){
 const nav=document.getElementById('nav');
 if(!nav) return;
 const secs=[...document.querySelectorAll('main > section')];
 secs.forEach((s,i)=>{ if(!s.id) s.id='sec-'+i; });
 nav.innerHTML='<span class="hint" style="margin:2px 6px 0 0">'+esc(T.nav)+':</span>'+
  secs.map(s=>'<a href="#'+s.id+'">'+esc((s.querySelector('h2')||{}).textContent? s.querySelector('h2').textContent.replace('▾','').replace('▸','').trim():'')+'</a>').join('');
 secs.forEach(s=>{
  const h=s.querySelector('h2');
  if(!h||h.dataset.fold) return;
  h.dataset.fold='1';
  h.insertAdjacentHTML('beforeend',' <span class="fold">▾</span>');
  h.addEventListener('click',()=>{
   s.classList.toggle('collapsed');
   const f=h.querySelector('.fold');
   if(f) f.textContent=s.classList.contains('collapsed')?'▸':'▾';
  });
 });
 if(!window.__navObs&&'IntersectionObserver' in window){
  window.__navObs=new IntersectionObserver(list=>{
   list.forEach(entry=>{
    const link=document.querySelector('nav a[href="#'+entry.target.id+'"]');
    if(link) link.classList.toggle('on',entry.isIntersecting);
   });
  },{rootMargin:'-40% 0px -50% 0px'});
  secs.forEach(s=>window.__navObs.observe(s));
 }
}
function showLogs(element,source){
 const sel=document.getElementById('logSrc');
 const inp=document.getElementById('logEl');
 if(sel&&source!==undefined) sel.value=source||'';
 if(inp) inp.value=element||'';
 loadLogs();
 const sec=document.getElementById('logs')?document.getElementById('logs').closest('section'):null;
 if(sec){sec.classList.remove('collapsed');sec.scrollIntoView({behavior:'smooth',block:'start'});}
}
function fmtSec(value){
 const s=Number(value||0);
 if(s<60) return s+' с';
 if(s<3600) return Math.round(s/60)+' мин';
 return (s/3600).toFixed(1)+' ч';
}
async function loadProject(){
 const p=await j('/api/project');
 setBlock(document.getElementById('project'), p.path
  ? '<p><code>'+esc(p.path)+'</code></p><pre>'+esc(JSON.stringify(p.data,null,2))+'</pre>'
  : '<p>'+esc(T.projNone)+'</p>');
}
async function loadProviders(){
 const r=await j('/api/providers');
 const items=r.providers||[];
 const box=document.getElementById('providers');
 setTxt(document.getElementById('provHint'),T.provHint||'');
 if(!items.length){boxEmpty(box,'<p>'+esc(T.noData)+'</p>');return}
 const tbl=ensureTable(box,[T.thName,T.thTitle,T.provAvail,T.provEnabled,''],'fx.providers');
 patchRows(tbl.tBodies[0],items,p=>p.name,p=>{
  const cmd=p.command?'<br><small><code>'+esc(p.command)+'</code></small>':'';
  const btn=p.enabled
   ?'<button class="warn" onclick="toggleProvider(&#39;'+esc(p.name)+'&#39;,false)">'+esc(T.provOff)+'</button>'
   :'<button onclick="toggleProvider(&#39;'+esc(p.name)+'&#39;,true)">'+esc(T.provOn)+'</button>';
  return [
   {h:'<code>'+esc(p.name)+'</code>'},
   {h:esc(p.title||'')+cmd},
   {h:p.available?T.on:T.off,c:p.available?'on':'off'},
   {h:p.enabled?T.on:T.off,c:p.enabled?'on':'off'},
   {h:btn}
  ];
 });
}
async function toggleProvider(name,enabled){
 await fetch('/api/providers/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,enabled})});
 loadProviders();
}
async function loadQuotas(){
 const q=await j('/api/quotas');
 const box=document.getElementById('quotas');
 if(q.available===false){setBlock(box,'<p>'+esc(q.note||'')+'</p>');return}
 const rows=Object.entries(q.providers||{});
 if(!rows.length){boxEmpty(box,'<p>'+esc(T.noData)+'</p>');return}
 const tbl=ensureTable(box,[T.thProvider,T.thWindow,T.thLimit,T.thUsed,T.thRemaining],'fx.quotas');
 patchRows(tbl.tBodies[0],rows,pair=>pair[0],pair=>{
  const r=pair[1]||{};
  return [
   {h:esc(pair[0])},
   {h:esc(r.window||'')},
   {h:r.limit==null?'∞':String(r.limit)},
   {h:String(r.used==null?0:r.used)},
   {h:(r.remaining==null?'∞':String(r.remaining))+(r.percent==null?'':' ('+r.percent+'%)')}
  ];
 });
}
async function loadMonitoring(){
 const inv=await j('/api/inventory');
 const s=(inv&&inv.summary)||{};
 setBlock(document.getElementById('monitor'),'<p>'+esc(T.invSummary
  .replace('{present}',s.repos_present==null?0:s.repos_present).replace('{total}',s.repos_total==null?0:s.repos_total)
  .replace('{tools}',s.tools==null?0:s.tools).replace('{providers}',s.providers==null?0:s.providers))+'</p>');
}
async function loadCatalog(){
 const c=await j('/api/catalog');
 const box=document.getElementById('catalog');
 const items=c.items||[];
 if(!items.length){boxEmpty(box,'<p>'+esc(T.catalogEmpty)+'</p>');return}
 const tbl=ensureTable(box,[T.thDomain,T.thGoal,T.thUpdated,T.thState],'fx.catalog');
 patchRows(tbl.tBodies[0],items,x=>(x.domain||'-')+'/'+x.name,x=>[
  {h:esc(x.domain||'-')+'/<code>'+esc(x.name)+'</code>'},
  {h:esc(String(x.goal||'')).slice(0,60)},
  {h:'<small>'+esc((x.updated||'').slice(0,19))+'</small>'},
  {h:x.ok?T.on:T.off,c:x.ok?'on':'off'}
 ]);
}
async function loadDoctor(){
 const d=await j('/api/doctor');
 const adpts=d.adapters||[], ints=d.integrations||[];
 const ready=adpts.filter(x=>x.available).length;
 const readyInt=ints.filter(x=>x.available).length;
 setTxt(document.getElementById('doctorSum'),T.doctorSummary
  .replace('{ready}',ready).replace('{total}',adpts.length)
  .replace('{iready}',readyInt).replace('{itotal}',ints.length));
 const all=adpts.map(x=>[x,'adapter']).concat(ints.map(x=>[x,'integration']));
 const box=document.getElementById('doctor');
 if(!all.length){boxEmpty(box,'<p>'+esc(T.noData)+'</p>');return}
 const tbl=ensureTable(box,[T.thName,T.thState],'fx.doctor');
 patchRows(tbl.tBodies[0],all,pair=>String(pair[0].adapter||pair[0].integration||''),pair=>{
  const x=pair[0], kind=pair[1];
  const name=x.adapter||x.integration||'—';
  const skip=['adapter','integration','available'];
  const details=Object.entries(x).filter(e=>skip.indexOf(e[0])<0&&e[1]!==null&&e[1]!==''&&!(Array.isArray(e[1])&&!e[1].length))
   .map(e=>'<div><small><b>'+esc(e[0])+'</b>: '+esc(Array.isArray(e[1])?e[1].join(', '):String(e[1]))+'</small></div>').join('');
  return [
   {h:'<code class="clickable" onclick="showLogs(&#39;'+esc(name)+'&#39;)">'+esc(name)+'</code> <small>'+kind+'</small>'+details},
   {h:x.available?T.on:T.off,c:x.available?'on':'off'}
  ];
 });
}
async function loadArsenal(){
 const a=await j('/api/arsenal');
 const s=a.summary||{};
 const cls={active:'on','need-guard':'wait',fault:'off'};
 setTxt(document.getElementById('arsenalSum'),T.arsenalSummary
  .replace('{working}',s.working==null?0:s.working).replace('{total}',s.total==null?0:s.total)
  .replace('{attention}',s.attention==null?0:s.attention).replace('{mode}',a.mode||''));
 const box=document.getElementById('arsenal');
 const nodes=a.nodes||[];
 if(!nodes.length){boxEmpty(box,'<p>'+esc(T.noData)+'</p>');return}
 const tbl=ensureTable(box,[T.thName,T.thClass,T.thState,T.thOwner,T.thDetail,''],'fx.arsenal');
 patchRows(tbl.tBodies[0],nodes,n=>n.id,n=>{
  const canAct=n['class']==='service'&&n.meta&&n.meta.start&&n.meta.start.length;
  const ctl=canAct?'<button onclick="act(&#39;'+esc(n.id)+'&#39;,&#39;restart&#39;)">↻</button>':'';
  return [
   {h:'<code class="clickable" onclick="showLogs(&#39;'+esc(n.name)+'&#39;)">'+esc(n.name)+'</code>'},
   {h:esc(n['class'])},
   {h:esc(n.state),c:cls[n.state]||''},
   {h:esc(n.owner||'')},
   {h:'<small>'+esc(n.detail||'')+'</small>'},
   {h:ctl}
  ];
 });
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
 const r=await j('/api/logs?limit=200'+(src?('&source='+encodeURIComponent(src)):'')+(el?('&element='+encodeURIComponent(el)):''));
 const box=document.getElementById('logs');
 if(!box.querySelector('.logctl')){
  box.innerHTML='<p class="logctl"><span id="logTitle"></span>'+
   '<select id="logSrc" onchange="loadLogs()"><option value="">'+esc(T.logsAll)+'</option></select> '+
   '<input id="logEl" style="width:auto" placeholder="'+esc(T.logsPh)+'"> '+
   '<button onclick="loadLogs()">'+esc(T.refresh)+'</button></p>'+
   '<pre id="logPre" style="max-height:320px;overflow:auto"></pre>';
  box._fxHtml=null;
 }
 const sel2=document.getElementById('logSrc');
 const opts=(r.sources||[]).map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+(x.available?'':' (—)')+'</option>').join('');
 if(sel2&&sel2._sig!==opts){
  sel2.innerHTML='<option value="">'+esc(T.logsAll)+'</option>'+opts;
  sel2._sig=opts;
 }
 if(sel2) sel2.value=src;
 const inp2=document.getElementById('logEl');
 if(inp2) inp2.value=el;
 setTxt(document.getElementById('logTitle'),el?esc(T.logsFiltered.replace('{name}',el)):'');
 const pre=document.getElementById('logPre');
 if(pre) pre.textContent=(r.lines||[]).map(x=>'['+x.source+'] '+x.line).join(String.fromCharCode(10));
}
async function loadInfra(){
 const g=await j('/api/infra');
 const box=document.getElementById('infra');
 if(!box) return;
 const shotBtn='<button onclick="shotGraph()">'+esc(T.infraShot)+'</button> <span id="shotMsg" class="hint"></span>';
 if(!g.total){
  box.innerHTML='<p class="hint">'+esc(T.infraEmpty)+'</p>'+
   '<p><small>'+esc(g.path||'')+'</small></p>'+shotBtn;
  return;
 }
 const byClass={};
 g.nodes.forEach(n=>{(byClass[n['class']]=byClass[n['class']]||[]).push(n)});
 const cols=Object.keys(byClass).map(c=>
  '<div class="infra-col"><h4>'+esc((g.class_titles||{})[c]||c)+' ('+byClass[c].length+')</h4>'+
  byClass[c].map(n=>'<div class="node" onclick="showLogs(\\''+esc(n.id)+'\\')">'+
    '<b>'+esc(n.id)+'</b> <span class="chip '+esc(n.state)+'">'+esc(n.state)+'</span><br>'+
    '<small>очки '+n.points_percent+'% · интент '+esc(n.intent||'—')+' · владелец '+esc(n.owner||'—')+'</small><br>'+
    '<small>класс '+esc(n['class'])+' · простой '+fmtSec(n.idle_sec)+(n.pid?(' · pid '+esc(n.pid)):'')+'</small>'+
    (n.errors?('<br><small class="off">ошибок: '+n.errors+'</small>'):'')+
   '</div>').join('')+'</div>').join('');
 const states=Object.entries(g.counts||{}).map(([k,v])=>'<span class="chip '+esc(k)+'">'+esc(k)+': '+v+'</span>').join(' ');
 const cand=(g.candidates||[]).length
  ? '<ul>'+g.candidates.map(c=>'<li><code>'+esc(c.id)+'</code> <small>'+esc(JSON.stringify(c))+'</small></li>').join('')+'</ul>'
  : '<p class="hint">'+esc(T.infraNoCand)+'</p>';
 const ev=(g.events||[]).length
  ? '<pre style="max-height:220px;overflow:auto">'+g.events.map(x=>esc(x)).join('\\n')+'</pre>'
  : '<p class="hint">'+T.noData+'</p>';
 box.innerHTML=
  '<p>'+states+'</p>'+
  '<p class="hint"><small>'+esc(String(g.saved_at||'').slice(0,19))+' · '+esc(T.infraNodes)+': '+g.total+' · '+esc(g.path||'')+'</small></p>'+
  '<div class="infra-grid">'+cols+'</div>'+
  '<p>'+shotBtn+'</p>'+
  '<img class="shot" id="shotImg" src="/api/infra/shot.png?t='+Date.now()+'" style="'+(g.shot_exists?'':'display:none')+'" onerror="this.style.display=\\'none\\'">'+
  '<details open><summary>'+esc(T.infraCandidates)+'</summary>'+cand+'</details>'+
  '<details><summary>'+esc(T.infraEvents)+'</summary>'+ev+'</details>';
}
async function shotGraph(){
 const msg=document.getElementById('shotMsg');
 if(msg) msg.textContent='…';
 try{
  const r=await fetch('/api/infra/shot',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const d=await r.json();
  if(msg) msg.textContent=d.ok?'готово':('ошибка: '+(d.error||''));
  if(d.ok){const img=document.getElementById('shotImg');if(img){img.style.display='';img.src='/api/infra/shot.png?t='+Date.now();}}
 }catch(e){ if(msg) msg.textContent='ошибка: '+e; }
}
load();loadJobs();loadSessions();loadProject();loadProviders();loadQuotas();loadMonitoring();loadInfra();loadCatalog();loadDoctor();loadArsenal();loadLogs();
setInterval(loadJobs,2000);
setInterval(function(){loadArsenal();loadInfra();loadMonitoring();},5000);
setInterval(function(){loadDoctor();loadQuotas();loadProviders();loadLedger();},15000);
setInterval(function(){loadSessions();loadCatalog();},30000);
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
        "t_tab_ui": tr("web.tab_ui"),
        "t_tab_auto": tr("web.tab_auto"),
        "t_tab_net": tr("web.tab_net"),
        "t_tab_service": tr("web.tab_service"),
        "t_font_size": tr("web.font_size"),
        "t_save": tr("web.save"),
        "t_t_host": tr("web.t_host"),
        "t_t_port": tr("web.t_port"),
        "t_t_refresh_all": tr("web.t_refresh_all"),
        "t_t_service_hint": tr("web.t_service_hint"),
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
        "t_web_nav": tr("web.nav"),
        "t_infra": tr("web.infra"),
        "t_infra_shot": tr("web.infra_shot"),
        "t_infra_empty": tr("web.infra_empty"),
        "t_infra_nodes": tr("web.infra_nodes"),
        "t_infra_events": tr("web.infra_events"),
        "t_infra_candidates": tr("web.infra_candidates"),
        "t_infra_no_candidates": tr("web.infra_no_candidates"),
        "t_logs_filtered": tr("web.logs_filtered"),
        "t_ledger_empty": tr("web.ledger_empty"),
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
                    "font_size": settings.get("ui.font_size", ""),
                    "web_host": settings.get("ui.web.host", "127.0.0.1"),
                    "web_port": settings.get("ui.web.port", ""),
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
            if path == "/api/infra":
                from .. import infragraph
                return self._send(infragraph.load(settings))
            if path == "/api/infra/shot.png":
                from .. import infragraph
                shot = infragraph.shot_path(settings)
                if not shot.exists():
                    return self._send({"error": "снимок графа ещё не сделан"}, status=404)
                try:
                    data = shot.read_bytes()
                except OSError as exc:
                    return self._send({"error": str(exc)}, status=500)
                return self._send(data, content_type="image/png")
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
            if path == "/api/infra/shot":
                from .. import infragraph
                _, post_settings, _, _ = build_context()
                result = infragraph.snapshot(post_settings)
                return self._send(result, status=200 if result.get("ok") else 400)
            return self._send({"error": "not found"}, status=404)

    return Handler


def serve(host: str | None = None, port: int | None = None,
          open_browser: bool = False) -> int:
    _, settings, _, _ = build_context()
    tr = make_translator(settings)
    host = host or str(settings.get("ui.web.host", "127.0.0.1"))
    port = port or int(settings.get("ui.web.port", 8787))
    server = ThreadingHTTPServer((host, port), _handler_class())
    print(tr("web.serving", host=host, port=port))
    url = f"http://{host}:{port}/"
    try:
        from ..single import open_dashboard, record_instance

        record_instance(url)
        if open_browser:
            # Явная просьба показать дашборд: сначала активируем уже открытую
            # вкладку (если браузер с отладкой), и только потом — новая.
            how = open_dashboard(url)
            if how == "opened":
                print(tr("web.browser_opened", url=url))
            elif how == "reused":
                print(tr("web.browser_reused", url=url))
    except Exception:  # noqa: BLE001 — открытие браузера не критично
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + tr("web.stopped"))
    finally:
        server.server_close()
    return 0
