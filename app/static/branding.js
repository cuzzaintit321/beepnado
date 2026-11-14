/* Public branding and workspace management. No remote image embedding or raw HTML. */
async function renderBranding(selected){
 const root=$('work-content');
 root.innerHTML='<div class="panel">Loading branding…</div>';
 try{
  const pages=await api('/api/branding/pages');
  if(view!=='branding')return;
  if(!pages.length){root.innerHTML='<div class="panel"><h3>No status pages yet</h3><p>Create a public or client status page first.</p><button class="work-btn" data-view="status">Create status page</button></div>';return;}
  const current=pages.find(p=>p.kind+':'+p.id===selected)||pages[0];
  const key=current.kind+':'+current.id;
  const path='/api/branding/'+current.kind+'/'+Number(current.id);
  const data=await api(path);
  if(view!=='branding')return;
  const b=data.branding, paid=user.plan!=='free', white=['team','scale'].includes(user.plan);
  const options=(values,selected)=>values.map(x=>'<option '+(x===selected?'selected':'')+' value="'+esc(x)+'">'+esc(x)+'</option>').join('');
  const when=v=>v?esc(fmt(new Date(v*1000).toISOString())):'Not verified';
  root.innerHTML='<div class="panel"><h3>Choose status page</h3><label>Page<select id="brand-page">'+pages.map(p=>'<option value="'+esc(p.kind+':'+p.id)+'" '+(p.kind+':'+p.id===key?'selected':'')+'>'+esc(p.title+' · '+p.kind)+'</option>').join('')+'</select></label><p><a class="work-btn" href="/s/'+esc(current.slug)+'" target="_blank" rel="noopener">Open published page ↗</a></p></div>'+
   '<div class="work-columns section-spaced"><div class="panel"><h3>Brand identity</h3>'+(!paid?'<p>Custom branding requires a paid plan.</p>':'')+'<form class="work-form" id="brand-form"><fieldset '+(paid?'':'disabled')+'><label>Brand name<input name="brand_name" maxlength="70"></label><label>Logo text / initials<input name="logo_text" maxlength="8"></label><label>Theme<select name="theme">'+options(['default','light','dark'],b.theme)+'</select></label><label>Accent<select name="color">'+options(['orange','blue','green','purple','slate'],b.color)+'</select></label><label>Footer text<textarea name="footer" maxlength="250"></textarea></label><label><input name="show_groups" type="checkbox"> Publish client group labels (review for private names first)</label><label><input name="hide_brand" type="checkbox" '+(white?'':'disabled')+'> Hide BeepNado branding (Team / Scale)</label><button class="btn btn-accent">Save branding</button></fieldset></form>'+ (paid?'<h3>Images</h3>'+['logo','favicon'].map(k=>'<div class="panel section-spaced"><label>'+k+' PNG (40 KB maximum)<input type="file" data-brand-file="'+k+'" accept="image/png"></label><button class="work-btn" data-image-save="'+k+'">Upload</button> <button class="work-btn" data-image-remove="'+k+'">Remove</button><p>'+ (b['has_'+k]?'Image uploaded':'No image uploaded')+'</p></div>').join(''):'')+'</div>'+
   '<div class="panel"><h3>Custom domains</h3><p>Workspace limit: '+data.domain_limit+'. DNS verification and operator activation are required.</p><form class="work-form" id="domain-form"><label>Hostname<input name="hostname" placeholder="status.example.com" required maxlength="253"></label><button class="work-btn" '+(data.domain_limit?'':'disabled')+'>Add domain</button></form>'+
   data.domains.map(d=>'<div class="panel section-spaced"><strong>'+esc(d.hostname)+'</strong><p>TXT record name: <code>'+esc('_beepnado.'+d.hostname)+'</code></p><label>TXT value<input readonly value="'+esc('beepnado='+d.token)+'"></label><p>Verified: '+when(d.verified_at)+' · '+(d.activated_at?'Activation recorded; HTTPS routing must be configured':'Awaiting operator activation')+'</p><button class="work-btn" data-domain-verify="'+Number(d.id)+'">Verify DNS</button> <button class="work-btn" data-domain-remove="'+Number(d.id)+'">Remove</button></div>').join('')+'</div></div>'+
   '<div class="work-columns section-spaced"><div class="panel"><h3>Publish announcement</h3><form class="work-form" id="announce-form"><label>Type<select name="kind">'+options(['incident','maintenance','update'],'update')+'</select></label><label>Title<input name="title" required minlength="3" maxlength="100"></label><label>Public message<textarea name="body" required maxlength="2000"></textarea></label><label><input name="notify_subscribers" type="checkbox"> Email confirmed subscribers (if enabled)</label><button class="btn btn-accent">Publish</button></form></div><div class="panel"><h3>Audience</h3><p>Confirmed subscribers: '+Number(data.subscribers)+' / '+Number(data.subscriber_limit)+'</p><p>Page loads in recorded days: '+data.views.reduce((n,v)=>n+Number(v.views),0)+'</p><p class="billing-note">Counts are page loads, not unique visitors.</p>'+data.views.slice(0,10).map(v=>'<p>'+esc(new Date(v.day*86400000).toISOString().slice(0,10))+' · '+Number(v.views)+'</p>').join('')+'</div></div>'+
   '<div class="panel section-spaced"><h3>Announcements</h3>'+(data.announcements.map(a=>'<div class="panel section-spaced"><strong>'+esc(a.title)+'</strong><p>'+esc(a.kind)+' · '+when(a.created_at)+'</p><p>'+esc(a.body)+'</p><button class="work-btn" data-announcement-remove="'+Number(a.id)+'">Remove</button></div>').join('')||'<p>No announcements yet.</p>')+'</div>';
  root.querySelector('#brand-page').onchange=e=>renderBranding(e.target.value);
  const f=root.querySelector('#brand-form');for(const k of ['brand_name','logo_text','footer'])f.elements[k].value=b[k]||'';f.elements.hide_brand.checked=b.hide_brand;f.elements.show_groups.checked=b.show_groups;
  f.onsubmit=async e=>{e.preventDefault();try{const body=Object.fromEntries(new FormData(f));body.hide_brand=f.elements.hide_brand.checked;body.show_groups=f.elements.show_groups.checked;await api(path,{method:'PUT',body:JSON.stringify(body)});toast('Branding saved');renderBranding(key);}catch(err){toast(err.message);}};
  for(const btn of root.querySelectorAll('[data-image-save]'))btn.onclick=async()=>{try{const k=btn.dataset.imageSave;const file=root.querySelector('[data-brand-file="'+k+'"]').files[0];if(!file||file.type!=='image/png'||file.size>40000)throw new Error('Choose a PNG of at most 40 KB');const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(const v of bytes)raw+=String.fromCharCode(v);await api(path+'/images/'+k,{method:'PUT',body:JSON.stringify({png:btoa(raw)})});renderBranding(key);}catch(e){toast(e.message);}};
  for(const btn of root.querySelectorAll('[data-image-remove]'))btn.onclick=async()=>{try{await api(path+'/images/'+btn.dataset.imageRemove,{method:'PUT',body:JSON.stringify({png:''})});renderBranding(key);}catch(e){toast(e.message);}};
  const d=root.querySelector('#domain-form');d.onsubmit=async e=>{e.preventDefault();try{await api(path+'/domains',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(d)))});renderBranding(key);}catch(err){toast(err.message);}};
  const a=root.querySelector('#announce-form');a.onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(a));body.notify_subscribers=a.elements.notify_subscribers.checked;if(!confirm('Publish this message publicly'+(body.notify_subscribers?' and queue subscriber email':'')+'?'))return;try{await api(path+'/announcements',{method:'POST',body:JSON.stringify(body)});renderBranding(key);}catch(err){toast(err.message);}};
  for(const btn of root.querySelectorAll('[data-domain-verify]'))btn.onclick=async()=>{try{await api(path+'/domains/'+Number(btn.dataset.domainVerify)+'/verify',{method:'POST'});toast('DNS verified');renderBranding(key);}catch(e){toast(e.message);}};
  for(const btn of root.querySelectorAll('[data-domain-remove]'))btn.onclick=async()=>{if(confirm('Remove this domain registration?'))try{await api(path+'/domains/'+Number(btn.dataset.domainRemove),{method:'DELETE'});renderBranding(key);}catch(e){toast(e.message);}};
  for(const btn of root.querySelectorAll('[data-announcement-remove]'))btn.onclick=async()=>{if(confirm('Remove this public announcement?'))try{await api(path+'/announcements/'+Number(btn.dataset.announcementRemove),{method:'DELETE'});renderBranding(key);}catch(e){toast(e.message);}};
 }catch(e){if(view==='branding')root.textContent=e.message;}
}
function enhancePublicStatus(s,custom=false){
 const b=s.branding||{}, page=document.getElementById('public-status'), body=document.getElementById('status-body');
 page.classList.remove('bn-status-light','bn-status-dark','bn-accent-orange','bn-accent-blue','bn-accent-green','bn-accent-purple','bn-accent-slate');
 if(['light','dark'].includes(b.theme))page.classList.add('bn-status-'+b.theme);
 if(['orange','blue','green','purple','slate'].includes(b.color))page.classList.add('bn-accent-'+b.color);
 const header=page.querySelector('.status-bar');
 if(header){
  header.replaceChildren();
  const brand=document.createElement('span');brand.className='bn-public-brand';
  brand.textContent=[b.logo_text,b.brand_name||(!b.hide_brand?'BeepNado':'Service status')].filter(Boolean).join(' · ');
  if(b.has_logo){const logo=document.createElement('img');logo.className='bn-public-logo';logo.alt=b.brand_name||'Status page logo';logo.src=(custom?'/api/custom-status':'/api/public-status/'+encodeURIComponent(s.slug))+'/images/logo';header.append(logo);}
  const subtitle=document.createElement('span');subtitle.textContent='Public service status';header.append(brand,subtitle);
  if(b.brand_name&&!b.hide_brand){const by=document.createElement('small');by.textContent='Powered by BeepNado';header.append(by);}
 }
 let favicon=document.querySelector('link[data-brand-favicon]');if(b.has_favicon){if(!favicon){favicon=document.createElement('link');favicon.rel='icon';favicon.dataset.brandFavicon='true';document.head.append(favicon);}favicon.href=(custom?'/api/custom-status':'/api/public-status/'+encodeURIComponent(s.slug))+'/images/favicon';}else if(favicon)favicon.remove();
 document.title=s.title+' — '+(b.brand_name||(!b.hide_brand?'BeepNado':'Service status'));
 const list=document.createElement('div');list.className='panel section-spaced';
 if(s.announcements?.length){
  list.innerHTML='<h3>Service announcements</h3>'+s.announcements.map(a=>'<article class="bn-status-announcement"><strong>'+esc(a.title)+'</strong><p>'+esc(a.kind)+' · '+esc(fmt(new Date(a.created_at*1000).toISOString()))+'</p><p>'+esc(a.body)+'</p></article>').join('');
  body.insertBefore(list,body.children[3]||null);
 }
 if(s.subscribe_enabled){
  const panel=document.createElement('div');panel.className='panel section-spaced';
  panel.innerHTML='<h3>Subscribe to announcements</h3><form class="work-form"><label>Email address<input name="email" type="email" required maxlength="160"></label><button class="work-btn">Subscribe</button></form><p class="billing-note" role="status">Confirmation is required. Unsubscribe at any time.</p>';
  const form=panel.querySelector('form');
  form.onsubmit=async e=>{e.preventDefault();try{const path=custom?'/api/custom-status/subscribe':'/api/public-status/'+encodeURIComponent(s.slug)+'/subscribe';const r=await api(path,{method:'POST',body:JSON.stringify({email:form.elements.email.value})});panel.querySelector('[role=status]').textContent=r.message;form.reset();}catch(err){panel.querySelector('[role=status]').textContent=err.message;}};
  body.append(panel);
 }
 if(b.footer){const footer=document.createElement('p');footer.className='bn-status-footer';footer.textContent=b.footer;body.append(footer);}
}
async function statusSubscriptionAction(){
 const match=location.hash.match(/^#status-(confirm|unsubscribe)=([A-Za-z0-9_-]{43})$/);
 if(!match)return false;
 show('status');
 const root=$('status-body');root.innerHTML='<div class="panel"><h2>'+ (match[1]==='confirm'?'Confirm subscription':'Unsubscribe')+'</h2><p>Choose below to '+(match[1]==='confirm'?'confirm your status page subscription.':'stop status announcement emails.')+'</p><button class="work-btn">Continue</button><p role="status"></p></div>';
 root.querySelector('button').onclick=async()=>{try{await api('/api/status-subscription/'+match[1],{method:'POST',body:JSON.stringify({token:match[2]})});history.replaceState(null,'','/');root.querySelector('[role=status]').textContent=match[1]==='confirm'?'Subscription confirmed.':'You have been unsubscribed.';root.querySelector('button').disabled=true;}catch(e){root.querySelector('[role=status]').textContent=e.message;}};
 return true;
}
