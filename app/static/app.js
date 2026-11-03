/* BeepNado UI: customer data and incident alerts come from the real API. */
const $ = id => document.getElementById(id);
const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let signedIn=false, user=null, view='overview', mode='register', dash=null, selectedPlan=null, resetToken=null, launchCapabilities=null;
const fmt = value => value ? new Date(value).toLocaleString() : 'Not checked';
const dateOnly = value => value ? new Date(value).toLocaleDateString() : '—';
const prettyState = s => s==='up'?'Operational':s==='down'?'Down':s==='degraded'?'Unconfirmed failure':'Awaiting first check';
const pill = state => `<span class="status-pill status-${esc(state)}">● ${prettyState(state)}</span>`;
const toast = (text) => {const e=$('toast');e.textContent=text;e.classList.add('show');clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove('show'),4500)};
async function api(path, opts={}){
  let headers={'Accept':'application/json',...(opts.body?{'Content-Type':'application/json'}:{}),...(opts.headers||{})};
  if(user?.csrf && opts.method && opts.method.toUpperCase()!=='GET')headers['X-CSRF-Token']=user.csrf;
  let response=await fetch(path,{credentials:'same-origin',...opts,headers});
  let data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status})`);
  return data;
}
function show(which){
 $('public-home').classList.toggle('hidden',which!=='home');
 $('account-screen').classList.toggle('hidden',which!=='auth');
 $('workspace').classList.toggle('hidden',which!=='workspace');
 $('public-status').classList.toggle('hidden',which!=='status');
 document.querySelector('.nav').classList.toggle('hidden',!['home','auth'].includes(which));
 $('site-footer').classList.toggle('hidden',which!=='home');
 window.scrollTo(0,0);
}
function openAuth(which='register'){
 mode=which;
 const messages={
   register:['Create your free account','Start watching your websites in a few clicks.','Create account ↗'],
   login:['Welcome back','Sign in to your monitoring workspace.','Sign in ↗'],
   forgot:['Reset your password','Enter your account email to receive a reset link.','Send reset email ↗'],
   reset:['Choose a new password','Use at least 12 characters for your new password.','Save new password ↗']
 };
 const [title,subtitle,submit]=messages[mode];
 $('account-title').textContent=title;
 $('account-subtitle').textContent=subtitle;
 $('account-submit').textContent=submit;
 $('account-swap').textContent=mode==='login'||mode==='forgot'||mode==='reset'?"Don't have an account? Create one":'Already have an account? Sign in';
 $('account-password').autocomplete=mode==='login'?'current-password':'new-password';
 $('account-password').required=mode!=='forgot';
 $('account-password').closest('label').hidden=mode==='forgot';
 $('account-email').required=mode!=='reset';
 $('account-email').closest('label').hidden=mode==='reset';
 $('account-forgot').hidden=mode!=='login';
 $('account-resend').hidden=mode!=='login';
 $('account-error').textContent='';
 $('account-success').textContent='';
 if(mode==='register' && launchCapabilities?.free_registration===false)
   $('account-error').textContent='Public registration is not open yet. Existing users can sign in.';
 show('auth');
}
async function refreshUser(){
 const response=await api('/api/me');signedIn=response.signed_in;user=response.user;
 return response;
}
async function loadDashboard(){
 await refreshUser();
 if(!signedIn){openAuth('login');return}
 dash=await api('/api/dashboard');
 if(!dash.monitors.length) view='monitors';
 $('user-chip').textContent=user.email+' · '+user.plan.toUpperCase();
 show('workspace');renderView();
}
function selectView(v){view=v;renderView();}
function renderView(){
 if(!dash)return;
 document.querySelectorAll('.side-link').forEach(e=>e.classList.toggle('active',e.dataset.view===view));
 const names={overview:'Overview',monitors:'Website monitors',incidents:'Incident history',alerts:'Alert feed',status:'Public status page',billing:'Plan & billing'};
 $('view-heading').textContent=names[view]||'Overview';$('view-crumb').textContent=(names[view]||'Overview').toUpperCase();
 if(view==='overview')return overview();
 if(view==='monitors')return monitors();
 if(view==='incidents')return incidents();
 if(view==='alerts')return alerts();
 if(view==='status')return statusSettings();
 if(view==='billing')return billing();
}
const empty = (title,text) => `<div class="empty-data"><strong>${title}</strong>${text}</div>`;
function rowsMonitor(list){
 if(!list.length)return empty('No monitors yet','Add your first website to begin collecting actual checks.');
 return `<div class="table-wrap"><table><thead><tr><th>Website</th><th>State</th><th>HTTP</th><th>Latency</th><th>Checks passed (30d)</th><th>Last checked</th><th></th></tr></thead><tbody>${list.map(m=>`<tr><td><strong>${esc(m.name)}</strong><div class="small-muted">${esc(m.url)}</div></td><td>${pill(m.state)}</td><td>${m.http_code??'—'}</td><td>${m.response_ms==null?'—':m.response_ms+' ms'}</td><td>${m.samples_30d?m.success_rate_30d+'% ('+m.samples_30d+')':'—'}</td><td>${fmt(m.last_checked)}</td><td><div class="table-actions"><button class="tiny-btn" data-check="${m.id}">Check</button><button class="tiny-btn" data-remove="${m.id}">Remove</button></div></td></tr>`).join('')}</tbody></table></div>`;
}
function overview(){
 const up=dash.monitors.filter(x=>x.state==='up').length, down=dash.monitors.filter(x=>x.state==='down').length;
 $('work-content').innerHTML=`<div class="work-cards"><div class="work-card"><span>ACTIVE MONITORS</span><strong>${dash.monitors.length}</strong><small>${dash.limit} maximum on ${esc(dash.plan)}</small></div><div class="work-card"><span>OPERATIONAL</span><strong>${up}</strong><small>Last observed up</small></div><div class="work-card"><span>ACTIVE OUTAGES</span><strong>${down}</strong><small>Confirmed after 2 failures</small></div><div class="work-card"><span>CHECKS RECORDED</span><strong>${dash.checks.length}${dash.checks.length===150?'+':''}</strong><small>Latest recorded samples; check-based, not an SLA</small></div></div><div class="work-columns"><div class="panel"><div class="panel-header"><h3>Your websites</h3><button class="work-btn" data-view="monitors">Manage monitors ↗</button></div>${rowsMonitor(dash.monitors.slice(0,8))}</div><div class="panel"><h3>Recent observations</h3>${dash.checks.length?`<div class="table-wrap"><table><thead><tr><th>Monitor</th><th>Region</th><th>Result</th></tr></thead><tbody>${dash.checks.slice(0,9).map(c=>`<tr><td>${esc(c.monitor_name)}<div class="small-muted">${fmt(c.checked_at)}</div></td><td>${esc(c.region)}</td><td>${pill(c.state)}</td></tr>`).join('')}</tbody></table></div>`:empty('No checks yet','New monitors are checked automatically when the scheduler runs.')}</div></div>`;
}
function monitors(){
 $('work-content').innerHTML=`<div class="work-columns"><div class="panel"><div class="panel-header"><h3>Monitored websites (${dash.monitors.length}/${dash.limit})</h3></div>${rowsMonitor(dash.monitors)}</div><div class="panel"><h3>Add a public website</h3><form class="work-form" id="monitor-form"><label>Monitor name<input name="name" placeholder="Customer website" minlength="2" maxlength="100" required></label><label>Website URL<input name="url" type="url" placeholder="https://example.com" required maxlength="1500"></label><label><input type="checkbox" name="authorized" required> I own or am authorized to monitor this site.</label><button class="btn btn-accent" type="submit">Add monitor ↗</button></form><p class="work-form-hint">Only publicly resolving HTTP(S) domains are accepted. Checks don't follow redirects or reach private network addresses. Your first results arrive after the scheduler runs.</p></div></div>`;
 $('monitor-form').addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(e.currentTarget);try{await api('/api/monitors',{method:'POST',body:JSON.stringify({name:fd.get('name'),url:fd.get('url'),authorized:fd.get('authorized')==='on'})});toast('Monitor added. First check scheduled.');dash=await api('/api/dashboard');renderView();}catch(err){toast(err.message)}});
}
function incidents(){
 $('work-content').innerHTML=`<div class="panel"><div class="panel-header"><h3>Recorded incidents</h3><span class="small-muted">Events are recorded from measured checks</span></div>${dash.incidents.length?`<div class="table-wrap"><table><thead><tr><th>Monitor</th><th>Summary</th><th>Opened</th><th>Resolved</th></tr></thead><tbody>${dash.incidents.map(i=>`<tr><td><strong>${esc(i.monitor_name)}</strong></td><td>${esc(i.summary)}</td><td>${fmt(i.opened_at)}</td><td>${i.resolved_at?fmt(i.resolved_at):'<span class="status-pill status-down">OPEN</span>'}</td></tr>`).join('')}</tbody></table></div>`:empty('No incidents recorded','When checks confirm downtime, the incident will appear here.')}</div><div class="panel section-spaced"><h3>How incidents work</h3><p class="billing-note">Two consecutive failed primary-region checks open an incident. A successful primary check closes it. Historical records are observations, not an availability SLA; secondary regions can record their own observations when deployed.</p></div>`;
}
function alerts(){
 const notices=dash.notifications||[];
 $('work-content').innerHTML=`<div class="panel"><div class="panel-header"><h3>Outage & recovery feed</h3><span class="small-muted">Events from confirmed primary-region checks</span></div>${notices.length?`<div class="alert-list">${notices.map(n=>`<div class="alert-event"><div class="alert-orb ${n.kind==='outage'?'alert-orb-down':'alert-orb-up'}">${n.kind==='outage'?'!':'✓'}</div><div><strong>${esc(n.message)}</strong><p>${esc(n.monitor_name)} · ${fmt(n.created_at)}</p></div></div>`).join('')}</div>`:empty('Nothing to report','When BeepNado confirms an outage or a recovery, it shows up here.')}</div><div class="panel section-spaced"><h3>What about email and SMS?</h3><p class="billing-note">BeepNado records in-app outage and recovery events. Verified users can receive email when SMTP is configured; SMS is not yet available. Never rely on a single monitoring provider for emergency response.</p></div>`;
}
function statusSettings(){
 const sp=dash.status_page;let url=sp?location.origin+'/s/'+sp.slug:'';
 $('work-content').innerHTML=`<div class="work-columns"><div class="panel"><h3>Make your monitoring public</h3><p class="billing-note">Create a read-only status page displaying monitor names, observed states, and recent incident dates. Anyone with the link can view the information.</p><form class="work-form" id="status-form"><label>Page title<input name="title" minlength="3" maxlength="70" required placeholder="Acme Service Status" value="${esc(sp?.title??'')}"></label><label>Public URL slug<input name="slug" minlength="3" maxlength="40" required pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="acme-status" value="${esc(sp?.slug??'')}"></label><button class="btn btn-accent" type="submit">${sp?'Update':'Create'} status page ↗</button></form></div><div class="panel"><h3>Current public link</h3>${sp?`<div class="status-link"><span>${esc(url)}</span><a href="${esc('/s/'+sp.slug)}" target="_blank" rel="noopener" class="work-btn alt">Open ↗</a></div>`:empty('No status page yet','Create a page to share your current monitor health.')}<p class="work-form-hint">Public pages expose the monitors linked to your account. Avoid private customer names in public monitor labels.</p></div></div>`;
 $('status-form').addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(e.currentTarget);try{await api('/api/status-page',{method:'POST',body:JSON.stringify({title:fd.get('title'),slug:fd.get('slug')})});dash=await api('/api/dashboard');toast('Public status page saved');renderView();}catch(err){toast(err.message)}});
}
function billing(){
 const list=[['free','Free','$0','2 monitors / 5-minute checks'],['pro','Pro','$12','20 monitors / 1-minute checks'],['team','Team','$39','100 monitors / 1-minute checks']];
 $('work-content').innerHTML=`<div class="panel"><h3>Your account</h3><p><strong>${esc(user.email)}</strong> · ${pill('up')} <span class="small-muted">Current plan: ${esc(user.plan.toUpperCase())}</span></p><p class="billing-note">Your plan controls the number of monitors and primary check interval. Paid billing opens only if Stripe prices and webhooks are configured. Payment cannot be activated by a browser redirect alone.</p></div><div class="pricing-grid section-spaced">${list.map(([id,label,price,description])=>`<div class="price-card"><div class="plan-tag">${esc(label.toUpperCase())}</div><div class="price">${price}<span>/mo</span></div><p>${esc(description)}</p><button class="btn ${id===user.plan?'btn-outline-dark':'btn-accent'}" data-bill-plan="${id}" ${id===user.plan || (id!=='free'&&!launchCapabilities?.billing)?'disabled':''}>${id===user.plan?'Current plan':id==='free'?'Free tier':launchCapabilities?.billing?'Subscribe ↗':'Planned tier'}</button></div>`).join('')}</div>${user.stripe_customer?'<div class="panel section-spaced"><button class="work-btn" id="billing-portal">Manage billing in Stripe ↗</button></div>':''}<p class="billing-note">Prices are proposed. No paid plan is available until a real Stripe account is configured and tested. Cancel or change existing subscriptions in Stripe Billing.</p>`;
 $('billing-portal')?.addEventListener('click',async()=>{try{const data=await api('/api/billing/portal',{method:'POST'});location.href=data.url}catch(e){toast(e.message)}});
}
async function openCheckout(plan){
 if(plan==='free'){toast('Free is available to new accounts. Contact support to downgrade an active paid subscription.');return}
 if(!launchCapabilities?.billing){toast('Paid plans are not yet available.');return}
 try{const d=await api('/api/billing/checkout',{method:'POST',body:JSON.stringify({plan})});location.href=d.url}catch(e){toast(e.message)}
}
async function renderPublicStatus(slug){
 show('status');
 try{
   const s=await api('/api/public-status/'+encodeURIComponent(slug));
   const bad=s.monitors.some(m=>m.state==='down');const pending=s.monitors.some(m=>m.state!=='up');
   $('status-body').innerHTML=`<span class="kicker">SERVICE HEALTH</span><h1>${esc(s.title)}</h1><p>Last loaded: ${fmt(s.observed_at)}</p><div class="status-banner ${bad?'alert':''}">${bad?'● One or more services are currently reporting downtime':pending?'◌ Some services have not confirmed a healthy state':'✓ All monitored services are operational'}</div><div class="panel"><h3>Services</h3>${s.monitors.length?`<div class="table-wrap"><table><thead><tr><th>Service</th><th>Status</th><th>Last checked</th></tr></thead><tbody>${s.monitors.map(m=>`<tr><td><strong>${esc(m.name)}</strong></td><td>${pill(m.state)}</td><td>${fmt(m.last_checked)}</td></tr>`).join('')}</tbody></table></div>`:empty('No public services listed','The account has not added a monitor.')}</div><div class="panel"><h3>Recent incidents</h3>${s.incidents.length?`<div class="table-wrap"><table><thead><tr><th>Service</th><th>Opened</th><th>Recovered</th></tr></thead><tbody>${s.incidents.map(i=>`<tr><td>${esc(i.monitor)}</td><td>${fmt(i.opened_at)}</td><td>${i.resolved_at?fmt(i.resolved_at):'Ongoing'}</td></tr>`).join('')}</tbody></table></div>`:empty('No incidents recorded','This page only displays incidents already observed by the monitor.')}</div><p class="billing-note">Displayed values reflect the most recent primary-region HTTP(S) observations, not a guaranteed SLA.</p>`;
 }catch(e){$('status-body').innerHTML=`<h1>Status page unavailable</h1><p>${esc(e.message)}</p>`}
}
async function init(){
 const statusMatch=location.pathname.match(/^\/s\/([a-z0-9-]+)$/);
 if(statusMatch){await renderPublicStatus(statusMatch[1]);return}
 try{launchCapabilities=await api('/api/availability')}catch(e){console.warn('Launch status unavailable')}
 try{await refreshUser()}catch(e){console.warn('Authentication status unavailable')}
 const start=async(which='register')=>{if(signedIn){await loadDashboard()}else openAuth(which)};
 ['nav-getstarted','hero-getstarted','how-start','closing-start','investigator-start'].forEach(id=>$(id)?.addEventListener('click',()=>start('register')));
 $('nav-signin')?.addEventListener('click',()=>start('login'));
 $('account-back')?.addEventListener('click',()=>show('home'));
 $('account-swap')?.addEventListener('click',()=>openAuth(mode==='login'?'register':'login'));
 $('account-forgot')?.addEventListener('click',()=>openAuth('forgot'));
 $('account-resend')?.addEventListener('click',async()=>{
   const email=$('account-email').value.trim();
   if(!email){$('account-error').textContent='Enter your email address first.';return}
   $('account-error').textContent='';
   try{const data=await api('/api/resend-verification',{method:'POST',body:JSON.stringify({email})});
       $('account-success').textContent=data.message||'Check your inbox.'}
   catch(err){$('account-error').textContent=err.message}
 });
 $('mobile-nav')?.addEventListener('click',()=>document.querySelector('.nav-menu').classList.toggle('open'));
 document.querySelectorAll('[data-plan]').forEach(b=>b.addEventListener('click',async()=>{selectedPlan=b.dataset.plan;await start('register');if(signedIn)openCheckout(selectedPlan)}));
 $('account-form').addEventListener('submit',async e=>{
   e.preventDefault(); $('account-error').textContent='';
   try{
     const email=$('account-email').value.trim(),password=$('account-password').value;
     if(mode==='register'){
       const data=await api('/api/register',{method:'POST',body:JSON.stringify({email,password})});
       $('account-success').textContent=data.message||'Check your inbox for a verification link.';
       $('account-form').reset();selectedPlan=null;
     }else if(mode==='login'){
       await api('/api/login',{method:'POST',body:JSON.stringify({email,password})});
       await loadDashboard();
       if(selectedPlan&&selectedPlan!=='free')await openCheckout(selectedPlan);
       selectedPlan=null;
     }else if(mode==='forgot'){
       const data=await api('/api/request-password-reset',{method:'POST',body:JSON.stringify({email})});
       $('account-success').textContent=data.message;
     }else if(mode==='reset'){
       await api('/api/reset-password',{method:'POST',body:JSON.stringify({token:resetToken,password})});
       history.replaceState(null,'',location.pathname+location.search);
       resetToken=null;openAuth('login');
       $('account-success').textContent='Password updated. Sign in with your new password.';
     }
   }catch(err){$('account-error').textContent=err.message}
 });
 document.querySelectorAll('.side-link').forEach(e=>e.addEventListener('click',()=>selectView(e.dataset.view)));
 $('signout')?.addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});user=null;signedIn=false;dash=null;show('home');toast('Signed out')}catch(e){toast(e.message)}});
 $('work-content').addEventListener('click',async e=>{
   const b=e.target.closest('button');if(!b)return;
   if(b.dataset.view){selectView(b.dataset.view);return}
   if(b.dataset.billPlan){await openCheckout(b.dataset.billPlan);return}
   if(b.dataset.check){b.disabled=true;b.textContent='Checking...';try{await api('/api/monitors/'+b.dataset.check+'/check',{method:'POST'});dash=await api('/api/dashboard');renderView();toast('Check completed')}catch(e){toast(e.message);b.disabled=false;b.textContent='Check'}return}
   if(b.dataset.remove){if(!confirm('Remove this monitor and its check history?'))return;try{await api('/api/monitors/'+b.dataset.remove,{method:'DELETE'});dash=await api('/api/dashboard');renderView();toast('Monitor removed')}catch(e){toast(e.message)}}
 });
 const hash=location.hash;
 if(hash.startsWith('#verify=')){
   const token=hash.slice(8);
   history.replaceState(null,'',location.pathname+location.search);
   try{await api('/api/verify-email',{method:'POST',body:JSON.stringify({token})});await loadDashboard();toast('Email verified. Welcome to BeepNado!')}
   catch(e){openAuth('login');$('account-error').textContent=e.message}
 }else if(hash.startsWith('#reset=')){
   resetToken=hash.slice(7);
   history.replaceState(null,'',location.pathname+location.search);
   openAuth('reset');
 }else if(location.search.includes('signin'))openAuth('login');
 else if(location.search.includes('signup')){openAuth('register');if(launchCapabilities?.free_registration===false){$('account-error').textContent='Free signup is not open yet. Email verification must be configured first.'}}
 else if(signedIn){await loadDashboard()}else show('home');
}
// Refresh read-only monitoring views automatically while the workspace is open.
setInterval(async()=>{
  if(!signedIn || !dash || $('workspace').classList.contains('hidden'))return;
  if(!['overview','incidents','alerts'].includes(view))return;
  try{dash=await api('/api/dashboard');renderView()}catch(e){console.warn('Live refresh unavailable')}
},30000);
init().catch(e=>{console.error(e);toast('Unable to load the app. Please refresh.')});
