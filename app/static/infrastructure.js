/* Account-scoped infrastructure dashboard. Agent credentials are shown only once. */
async function renderInfrastructure(){
 const root=$('work-content');
 root.innerHTML='<div class="panel"><h3>Infrastructure</h3><p>Loading server agents…</p></div>';
 const pct=v=>Number.isFinite(v)?v.toFixed(1)+'%':'—';
 const stamp=v=>v?fmt(new Date(v*1000).toISOString()):'Never';
 const counter=v=>Number.isFinite(v)?(v/1024**3).toFixed(2)+' GiB':'—';
 function tokenDialog(name,token){
  const dialog=document.createElement('dialog');
  const config=JSON.stringify({url:location.origin,token,mounts:['/'],services:[],docker_names:[]},null,2);
  const command="umask 077\nmkdir -p \"$HOME/.config/beepnado\" \"$HOME/.local/bin\"\ncurl -fsSL "+location.origin+"/agent/linux.py -o \"$HOME/.local/bin/beepnado-agent.py\"\ncat > \"$HOME/.config/beepnado/agent.json\" <<'BEEPNADO_CONFIG'\n"+config+"\nBEEPNADO_CONFIG\npython3 \"$HOME/.local/bin/beepnado-agent.py\" --config \"$HOME/.config/beepnado/agent.json\"";
  dialog.className='bn-investigator';
  dialog.innerHTML='<h2>Connect '+esc(name)+'</h2><p>Run on the monitored Linux server. This starts the agent in the foreground. Keep its token private; rotating it invalidates the old configuration.</p><textarea readonly rows="14" aria-label="Linux agent install commands"></textarea><p><button class="work-btn" data-copy>Copy commands</button> <button class="work-btn" data-close>Close</button></p>';
  dialog.querySelector('textarea').value=command;
  dialog.querySelector('[data-copy]').onclick=async()=>{try{await navigator.clipboard.writeText(command);toast('Agent configuration copied');}catch(e){toast('Select and copy the commands manually');}};
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  document.body.append(dialog);dialog.showModal();
 }
 function chart(samples,key,label){
  const rows=samples.filter(s=>Number.isFinite(s[key])).slice().reverse();
  if(!rows.length)return '<p>No '+esc(label)+' samples yet.</p>';
  const points=rows.map((s,i)=>(10+300*i/Math.max(1,rows.length-1)).toFixed(2)+','+(95-85*s[key]/100).toFixed(2)).join(' ');
  return '<h4>'+esc(label)+'</h4><svg viewBox="0 0 320 105" width="100%" role="img" aria-label="'+esc(label)+' history, scale 0 to 100 percent"><path d="M10 10V95H310" stroke="currentColor" opacity=".4" fill="none"/><polyline points="'+points+'" stroke="#f97343" stroke-width="2" fill="none"/>'+rows.map((s,i)=>'<circle cx="'+(10+300*i/Math.max(1,rows.length-1)).toFixed(2)+'" cy="'+(95-85*s[key]/100).toFixed(2)+'" r="2" fill="#f97343"><title>'+esc(stamp(s.received_at)+' · '+pct(s[key]))+'</title></circle>').join('')+'</svg><p class="billing-note">0–100% · '+rows.length+' measured samples</p>';
 }
 try{
  const servers=await api('/api/infrastructure');
  if(view!=='infrastructure')return;
  root.innerHTML='<div class="work-columns"><div class="panel"><h3>Registered servers</h3><p class="billing-note">One-minute agent reports. Missing reports are unverified, not confirmed server outages.</p>'+
  (servers.length?servers.map(s=>{const m=s.latest;return '<div class="panel section-spaced"><h3>'+esc(s.name)+'</h3><p>'+esc(s.state)+' · Last report '+esc(stamp(s.last_seen))+'</p>'+
   (m?'<p>'+esc(m.hostname)+' · '+esc(m.platform)+' · '+Number(m.cpu_count)+' CPUs · Uptime '+Math.floor(m.uptime_seconds/3600)+'h</p><div class="work-cards"><div class="work-card"><span>CPU</span><strong>'+pct(m.cpu_percent)+'</strong></div><div class="work-card"><span>RAM</span><strong>'+pct(m.memory_percent)+'</strong></div><div class="work-card"><span>SWAP</span><strong>'+pct(m.swap_percent)+'</strong></div></div><p>Load (1m): '+esc(m.load1??'—')+' · Network RX/TX totals: '+counter(m.network_rx_bytes)+' / '+counter(m.network_tx_bytes)+' · Disk read/write totals: '+counter(m.disk_read_bytes)+' / '+counter(m.disk_write_bytes)+'</p><h4>Storage</h4>'+m.disks.map(d=>'<p>'+esc(d.mount)+' · '+pct(d.used_percent)+' used · '+counter(d.free_bytes)+' free</p>').join('')+'<h4>Selected services and containers</h4>'+([...m.services,...m.containers].map(x=>'<p>'+esc(x.name)+' · '+esc(x.state)+(x.health?' · '+esc(x.health):'')+'</p>').join('')||'<p>None reported.</p>'):'<p>Waiting for agent registration.</p>')+
   '<p>Thresholds: CPU '+pct(s.cpu_threshold)+' · RAM '+pct(s.memory_threshold)+' · disk '+pct(s.disk_threshold)+'</p><button class="work-btn" data-history="'+Number(s.id)+'">Performance history</button> <button class="work-btn" data-edit="'+Number(s.id)+'">Edit thresholds</button> <button class="work-btn" data-rotate="'+Number(s.id)+'">Replace token</button> <button class="work-btn" data-delete="'+Number(s.id)+'">Remove</button><div id="server-history-'+Number(s.id)+'"></div></div>';}).join(''):empty('No servers registered','Register a Linux server to get started.'))+
  '</div><div class="panel"><h3>Register a server</h3><form class="work-form" id="server-form"><label>Name<input name="name" minlength="2" maxlength="100" required></label><label>CPU threshold (%)<input name="cpu_threshold" type="number" min="1" max="100" value="90" required></label><label>Memory threshold (%)<input name="memory_threshold" type="number" min="1" max="100" value="90" required></label><label>Disk threshold (%)<input name="disk_threshold" type="number" min="1" max="100" value="90" required></label><button class="btn btn-accent">Register server</button></form><p class="billing-note">Three consecutive warning samples trigger an alert through account-wide immediate notification channels. Three healthy reports clear it. History retained for seven days; the chart displays the latest 120 samples.</p></div></div>';
  const form=root.querySelector('#server-form');
  function settings(f){const v=Object.fromEntries(new FormData(f));for(const k of ['cpu_threshold','memory_threshold','disk_threshold'])v[k]=Number(v[k]);return v;}
  form.onsubmit=async e=>{e.preventDefault();const v=settings(form);const b=form.querySelector('button');b.disabled=true;try{const r=await api('/api/infrastructure',{method:'POST',body:JSON.stringify(v)});tokenDialog(v.name,r.token);renderInfrastructure();}catch(err){toast(err.message);b.disabled=false;}};
  root.querySelectorAll('[data-history]').forEach(b=>b.onclick=async()=>{try{const rows=await api('/api/infrastructure/'+b.dataset.history+'/history');const p=$('server-history-'+b.dataset.history);if(p)p.innerHTML=chart(rows,'cpu_percent','CPU usage')+chart(rows,'memory_percent','Memory usage');}catch(e){toast(e.message);}});
  root.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const s=servers.find(x=>x.id===Number(b.dataset.edit));const d=document.createElement('dialog');d.innerHTML='<h3>Edit server thresholds</h3><form class="work-form"><label>Name<input name="name" required maxlength="100"></label>'+['cpu','memory','disk'].map(k=>'<label>'+k+' (%)<input name="'+k+'_threshold" type="number" min="1" max="100" required></label>').join('')+'<button class="work-btn">Save</button></form><button class="work-btn" data-close>Cancel</button>';const f=d.querySelector('form');for(const k of ['name','cpu_threshold','memory_threshold','disk_threshold'])f.elements[k].value=s[k];f.onsubmit=async e=>{e.preventDefault();try{await api('/api/infrastructure/'+s.id,{method:'PUT',body:JSON.stringify(settings(f))});d.close();renderInfrastructure();}catch(e){toast(e.message);}};d.querySelector('[data-close]').onclick=()=>d.close();d.addEventListener('close',()=>d.remove(),{once:true});document.body.append(d);d.showModal();});
  root.querySelectorAll('[data-rotate]').forEach(b=>b.onclick=async()=>{if(!confirm('Invalidate the existing agent token?'))return;try{const s=servers.find(x=>x.id===Number(b.dataset.rotate));const r=await api('/api/infrastructure/'+s.id+'/rotate',{method:'POST'});tokenDialog(s.name,r.token);}catch(e){toast(e.message);}});
  root.querySelectorAll('[data-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Remove the server and all its metrics?'))return;try{await api('/api/infrastructure/'+b.dataset.delete,{method:'DELETE'});renderInfrastructure();}catch(e){toast(e.message);}});
 }catch(e){if(view==='infrastructure')root.textContent=e.message;}
}
