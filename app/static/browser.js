/* Browser monitoring evidence is private and separate from uptime incidents. */
async function renderBrowser(){
 const root=$('work-content');
 root.innerHTML='<div class="panel"><h3>Browser monitoring</h3><p>Loading…</p></div>';
 const ms=v=>Number.isFinite(v)?v+' ms':'—';
 const stamp=v=>fmt(new Date(v*1000).toISOString());
 function evidence(j,s){
  if(!s)return '<p>Awaiting a browser worker observation.</p>';
  return '<p>Observed: '+esc(stamp(s.checked_at))+' · '+esc(s.state)+' · '+esc(s.note)+'</p><p>HTTP '+esc(s.status??'—')+' · Load '+ms(s.load_ms)+' · FCP '+ms(s.fcp_ms)+' · LCP '+ms(s.lcp_ms)+' · Layout shift sum '+esc(s.cls??'—')+'</p><p>JavaScript errors: '+Number(s.js_errors)+' · Failed/blocked resources: '+Number(s.failed_resources)+' · Required text: '+(s.text_found===null?'Not configured':s.text_found?'Found':'Missing')+' · Visual difference: '+(Number.isFinite(s.visual_difference_percent)?s.visual_difference_percent+'%':'—')+'</p>'+
   (s.has_screenshot?'<img class="bn-browser-shot" loading="lazy" alt="Recorded website screenshot" src="/api/browser/'+Number(j.id)+'/image/'+Number(s.id)+(workspaceScope?'?workspace='+encodeURIComponent(workspaceScope):'')+'">':'<p>No screenshot captured.</p>');
 }
 try{
  const data=await api('/api/browser');
  if(view!=='browser')return;
  root.innerHTML='<div class="work-columns"><div class="panel"><h3>Browser monitors ('+data.jobs.length+'/'+data.limit+')</h3><p class="billing-note">Independent synthetic observations; never automatically change website uptime state. A worker must be deployed offsite.</p>'+
   (data.jobs.map(j=>'<div class="panel section-spaced"><h3>'+esc(j.name)+'</h3><p>'+esc(j.url)+' · '+esc(j.viewport)+'</p>'+evidence(j,j.latest)+'<button class="work-btn" data-browser-history="'+Number(j.id)+'">Screenshot history</button> <button class="work-btn" data-browser-remove="'+Number(j.id)+'">Remove</button><div id="browser-history-'+Number(j.id)+'"></div></div>').join('')||empty('No browser monitors','Register an approved public page to start collecting evidence.'))+
   '</div><div class="panel"><h3>Add browser monitor</h3>'+(!data.enabled?'<p>Browser monitoring is disabled. Operator must configure and verify the isolated worker first.</p>':'<form class="work-form" id="browser-form"><label>Name<input name="name" required minlength="2" maxlength="100"></label><label>Public URL<input name="url" type="url" required maxlength="1000" placeholder="https://example.com"></label><label>Viewport<select name="viewport"><option value="desktop">Desktop</option><option value="mobile">Mobile viewport</option></select></label><label>Required visible text (optional)<input name="required_text" maxlength="120"></label><label><input type="checkbox" name="authorized" required> I am authorized to monitor this page.</label><button class="btn btn-accent">Add browser monitor</button></form>')+
   '<p class="billing-note">15-minute target, seven-day retention. Images are reduced JPEG previews. Layout shift is an observed sum, not official field CLS. Visual differences use a coarse grayscale comparison; changing content can trigger differences. The worker only allows operator-approved origins and blocks other resources.</p></div></div>';
  const form=root.querySelector('#browser-form');
  if(form)form.onsubmit=async e=>{e.preventDefault();const fd=new FormData(form);const body=Object.fromEntries(fd);body.authorized=fd.has('authorized');const b=form.querySelector('button');b.disabled=true;try{await api('/api/browser',{method:'POST',body:JSON.stringify(body)});renderBrowser();}catch(err){toast(err.message);b.disabled=false;}};
  root.querySelectorAll('[data-browser-history]').forEach(b=>b.onclick=async()=>{try{const j=data.jobs.find(x=>x.id===Number(b.dataset.browserHistory));const rows=await api('/api/browser/'+j.id+'/history');const box=$('browser-history-'+j.id);if(box)box.innerHTML='<h4>Recent observations</h4>'+rows.map(s=>'<details><summary>'+esc(stamp(s.checked_at))+' · '+esc(s.state)+'</summary>'+evidence(j,s)+'</details>').join('');}catch(e){toast(e.message);}});
  root.querySelectorAll('[data-browser-remove]').forEach(b=>b.onclick=async()=>{if(!confirm('Delete this browser monitor and its screenshots?'))return;try{await api('/api/browser/'+b.dataset.browserRemove,{method:'DELETE'});renderBrowser();}catch(e){toast(e.message);}});
 }catch(e){if(view==='browser')root.textContent=e.message;}
}
