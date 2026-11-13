'use strict';
const $ = (id) => document.getElementById(id);
const themeToggle = $('theme-toggle');
function setTheme(theme) {document.documentElement.dataset.theme=theme; const dark=theme==='dark'; themeToggle.textContent=dark?'☀ Light mode':'☾ Dark mode'; themeToggle.setAttribute('aria-label',dark?'Switch to light mode':'Switch to dark mode'); themeToggle.setAttribute('aria-pressed',String(dark));}
try {setTheme(localStorage.getItem('swarmtenderr-theme')||'dark');} catch {setTheme('dark');}
themeToggle.addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';setTheme(theme);try{localStorage.setItem('swarmtenderr-theme',theme);}catch{}});
let data = null, history = {actions: [], samples: []}, filter = 'all', view = 'overview', selected = null, busy = false;
const bytes = (n) => { if (!Number.isFinite(n) || n < 0) return '—'; const units = ['B','KiB','MiB','GiB','TiB']; let i = 0; while(n >= 1024 && i < 4) {n /= 1024; i++;} return `${n.toFixed(i && n < 100 ? 1 : 0)} ${units[i]}`; };
const duration = (n) => n == null ? 'Unknown' : n < 60 ? '< 1 min' : n < 3600 ? `${Math.ceil(n/60)} min` : n < 86400 ? `${(n/3600).toFixed(1)} hr` : `${(n/86400).toFixed(1)} days`;
const date = (n) => n ? new Date(n * 1000).toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : 'Unknown';
function element(tag, text, className) {const node = document.createElement(tag); if(text != null) node.textContent = text; if(className) node.className = className; return node;}
function status(row) {if(row.progress >= 1) return ['Seeding','seeding']; if(row.state === 'downloading') return ['Downloading','downloading']; if(row.state === 'metaDL') return ['Metadata','metadata']; if(row.state.includes('paused') || row.state.includes('stopped')) return ['Paused','waiting']; if(row.state === 'stalledDL') return ['Stalled','waiting']; return [row.state === 'queuedDL' ? 'Queued' : row.state,'waiting'];}
function matches(row) {if(filter==='all') return true; if(filter==='downloading') return row.progress<1 && row.download_speed>0; if(filter==='seeding') return row.progress>=1; return row.progress<1 && row.download_speed===0;}
async function api(path, options) {const response = await fetch(path, {cache:'no-store', ...options}); const value = await response.json(); if(!response.ok) throw new Error(value.error || `Request failed (${response.status})`); return value;}
function notice(message) {$('notice').hidden = !message; $('notice').textContent = message;}
async function refresh() {if(busy) return; busy=true; $('refresh').disabled=true; try {const [result, activity] = await Promise.all([api('/api/v1/status'),api('/api/v1/activity')]); data=result; history=activity; render();} catch(error) {notice(error.message); $('connection').textContent='Connection unavailable';} finally {busy=false; $('refresh').disabled=false;}}
function render() {
  const snapshot=data.snapshot;
  $('version').textContent=`v${data.version}`;
  if(!snapshot){notice('Waiting for the first successful qBittorrent observation. Check the runtime connection settings.'); $('connection').textContent='Waiting for qBittorrent'; return;}
  const rows=snapshot.torrents, engine=snapshot.optimizer;
  $('connection').textContent=data.stale ? 'Observation is stale' : snapshot.demo ? 'Demo · sample torrents' : 'Connected to qBittorrent';
  notice(snapshot.demo ? 'DEMO WORKSPACE · These are sample open-content torrents. No download client is connected and all controls are disabled.' : data.stale || data.error ? data.error || 'The last observation is stale. Check the download client.' : engine.connected && engine.stale ? 'The optimizer snapshot is stale. Live torrent data remains independent.' : '');
  $('download').textContent=bytes(snapshot.download_speed)+'/s'; $('pulse-speed').textContent=bytes(snapshot.download_speed)+'/s';
  $('active').textContent=rows.filter(r=>r.progress<1 && r.download_speed>0).length;
  $('queued').textContent=rows.filter(r=>r.state==='queuedDL').length;
  $('seeding').textContent=rows.filter(r=>r.progress>=1).length;
  $('upload').textContent=bytes(snapshot.upload_speed)+'/s shared with the swarm';
  $('nav-count').textContent=rows.length; $('table-count').textContent=rows.length;
  $('updated').textContent='Observed '+date(snapshot.observed_at);
  if(engine.connected) {
    $('engine-badge').textContent=engine.stale ? 'STALE SNAPSHOT' : 'APOLLO OPTIMIZER';
    $('engine-title').textContent='Make every slot count.';
    const c=engine.capacity; $('engine-description').textContent=`${c.applied_slots} of ${c.requested_slots} requested download slots fit the current storage budgets. Select a torrent to understand its queue decision.`;
    $('engine-footnote').textContent=`SSD reserved: ${bytes(c.ssd_reserved_bytes)} of ${bytes(c.ssd_budget_bytes)} · Library: ${bytes(c.library_reserved_bytes)} of ${bytes(c.library_budget_bytes)}`;
    $('slots').textContent=`${c.applied_slots} allowed · ${c.requested_slots} requested slots`;
  }
  chart(); table(); activity(); settings();
}
function chart() {const samples=history.samples; if(!samples.length) return; const max=Math.max(1,...samples.map(s=>s.download)); const points=samples.map((s,i)=>`${samples.length===1?220:i/(samples.length-1)*440},${85-s.download/max*75}`); $('chart-line').setAttribute('d','M'+points.join(' L')); $('chart-area').setAttribute('d','M0 90 L'+points.join(' L')+' L440 90 Z'); $('chart-start').textContent=date(samples[0].at);}
function table() {
  const snapshot=data?.snapshot; if(!snapshot) return;
  let rows=snapshot.torrents.filter(r=>matches(r)&&r.name.toLowerCase().includes($('search').value.toLowerCase()));
  const sort=$('sort').value; rows.sort((a,b)=>sort==='speed'?b.download_speed-a.download_speed:sort==='progress'?b.progress-a.progress:sort==='name'?a.name.localeCompare(b.name):(a.priority<0?1e9:a.priority)-(b.priority<0?1e9:b.priority));
  const fragment=document.createDocumentFragment();
  for(const row of rows) {
    const tr=element('tr'), name=element('td'), wrap=element('div',null,'torrent-name'); wrap.append(element('span',row.progress>=1?'↑':'↓','torrent-symbol'));
    const label=element('div'); label.append(element('strong',row.name),element('small',`${row.category || 'Uncategorized'} · ${bytes(row.size)} · ${row.seeds} seeds`)); wrap.append(label); name.append(wrap);
    const progress=element('td'); progress.append(element('span',`${(row.progress*100).toFixed(1)}%`,'progress-label')); const bar=element('progress'); bar.max=1; bar.value=row.progress; bar.setAttribute('aria-label',`${row.name} progress`); progress.append(bar);
    const speed=element('td',row.download_speed>0?bytes(row.download_speed)+'/s':row.upload_speed>0?'↑ '+bytes(row.upload_speed)+'/s':'—');
    const state=element('td'); const [title,kind]=status(row); state.append(element('span',title,'badge '+kind));
    const action=element('td'), button=element('button','›','icon-button'); button.setAttribute('aria-label','Details for '+row.name); button.addEventListener('click',()=>details(row)); action.append(button);
    tr.append(name,progress,speed,element('td',row.progress>=1?'Complete':duration(row.eta_seconds)),state,action); fragment.append(tr);
  }
  $('torrent-rows').replaceChildren(fragment); $('empty').hidden=rows.length>0; $('showing').textContent=`Showing ${rows.length} of ${snapshot.torrents.length} torrents`;
}
function definitions(node, pairs) {node.replaceChildren(); for(const [key,value] of pairs) node.append(element('dt',key),element('dd',value));}
function details(row) {selected=row; $('detail-name').textContent=row.name; $('detail-reason').textContent=`${row.decision}: ${row.reason}`;
  definitions($('detail-values'),[['Progress',`${(row.progress*100).toFixed(1)}%`],['Queue position',row.priority<0?'Outside download queue':row.priority],['Download',bytes(row.download_speed)+'/s'],['Upload',bytes(row.upload_speed)+'/s'],['Time left',duration(row.eta_seconds)],['Availability',`${row.seeds} seeds · ${row.peers} peers`]]);
  const enabled=data.snapshot.controls&&!row.protected&&!data.stale;
  $('actions').querySelectorAll('button').forEach(b=>b.disabled=!enabled); $('control-note').textContent=enabled?'Requests apply to this torrent only. The next observation confirms the result.':row.protected?'Completed, force-started and keep-tagged torrents are protected.':'Observation mode. Queue controls are disabled for this deployment.'; $('action-result').textContent=''; $('details').showModal();}
function activity() {const list=$('activity-list'), events=[...history.actions.map(a=>({at:a.at,name:data?.snapshot?.torrents.find(r=>r.hash===a.torrent)?.name||a.torrent,action:a.action,reason:a.outcome})),...(data?.snapshot?.optimizer.events||[])].sort((a,b)=>b.at-a.at); list.replaceChildren(); if(!events.length) {list.append(element('p','No actions recorded yet. The log begins with your first request or connected optimizer decision.','subtle')); return;} for(const event of events.slice(0,100)){const row=element('div',null,'activity-item'), body=element('div'); body.append(element('strong',event.action+' · '+event.name),element('small',event.reason)); row.append(element('small',date(event.at)),body);list.append(row);}}
function settings() {if(!data?.snapshot) return; definitions($('settings-list'),[['Version',data.version],['Database',data.database==='sqlite'?'SQLite · local persistent storage':'PostgreSQL · external database'],['Client','qBittorrent 5 WebUI'],['Controls',data.snapshot.controls?'Manual controls enabled':'Observation only'],['Optimizer',data.snapshot.optimizer.connected?'Apollo controller · read-only decision feed':'Native queue · automatic policy not attached'],['Data retention','30 days of aggregate transfer samples; persistent action audit'],['Demo',data.snapshot.demo?'Enabled · sample data':'Disabled']]);}
const titles={overview:['Your queue, in flow.','A clear view of your torrents. A little more peace of mind.'],torrents:['A home for every transfer.','See what’s moving, what’s waiting, and why.'],activity:['Nothing lost in the swarm.','A durable record of requests and optimizer decisions.'],settings:['Your swarm. Your setup.','Self-hosted, transparent, and connected on your terms.']};
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{view=button.dataset.view; document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('selected',b===button)); $('view-name').textContent=view[0].toUpperCase()+view.slice(1); $('page-title').textContent=titles[view][0]; $('page-description').textContent=titles[view][1]; $('stats').hidden=!['overview','torrents'].includes(view); $('overview-panels').hidden=view!=='overview'; $('torrent-section').hidden=!['overview','torrents'].includes(view); $('activity-section').hidden=view!=='activity'; $('settings-section').hidden=view!=='settings';}));
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b===button));table();}));
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',async()=>{if(!selected)return; const torrent=selected; document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);try {const result=await api(`/api/v1/torrents/${torrent.hash}/actions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:button.dataset.action})}); $('action-result').textContent=result.message;}catch(error){$('action-result').textContent=error.message;}await refresh();}));
$('close-details').addEventListener('click',()=>$('details').close()); $('search').addEventListener('input',table); $('sort').addEventListener('change',table); $('refresh').addEventListener('click',refresh);
let socket, reconnectDelay=1000;
function connectLive() {
  socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/v1/live`);
  socket.addEventListener('open',()=>{reconnectDelay=1000;});
  socket.addEventListener('message',(event)=>{try {const message=JSON.parse(event.data); if(message.type==='snapshot'){data=message.status;history=message.activity;render();}}catch{notice('The live connection returned an unreadable update.');}});
  socket.addEventListener('close',()=>{$('connection').textContent='Reconnecting to live updates…';setTimeout(connectLive,reconnectDelay);reconnectDelay=Math.min(reconnectDelay*2,30000);});
  socket.addEventListener('error',()=>{socket.close();});
}
refresh().finally(connectLive);

