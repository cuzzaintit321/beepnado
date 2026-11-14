/* Explicit workspace scope per page, never a mutable shared session preference. */
async function renderTeams(){
 const root=$('work-content');
 const role=user.workspace_role||'owner';
 const manager=['owner','admin'].includes(role);
 const stamp=v=>fmt(new Date(v*1000).toISOString());
 root.innerHTML='<div class="panel"><h3>Team workspace</h3><p>Loading…</p></div>';
 try{
  const spaces=await api('/api/team/workspaces');
  const info=manager?await api('/api/team/roster'):null;
  const log=manager?await api('/api/team/audit'):[];
  if(view!=='teams')return;
  const roles=role==='owner'?['admin','operator','viewer']:['operator','viewer'];
  const opts=(selected)=>roles.map(r=>'<option value="'+r+'" '+(selected===r?'selected':'')+'>'+r+'</option>').join('');
  root.innerHTML='<div class="work-columns"><div class="panel"><h3>Workspaces</h3><p>Current: '+esc(user.workspace_name||'Personal workspace')+' · '+esc(role)+'</p><label>Switch workspace<select id="team-switch">'+spaces.map(s=>'<option value="'+Number(s.id)+'" '+(Number(s.id)===Number(user.workspace_id)?'selected':'')+'>'+esc(s.name)+' · '+esc(s.role)+'</option>').join('')+'</select></label><button class="work-btn" id="team-switch-button">Open workspace</button>'+
   (role==='owner'?'<form class="work-form section-spaced" id="team-name"><label>Workspace name<input name="name" required minlength="2" maxlength="100"></label><button class="work-btn">Save name</button></form>':'')+'</div>'+
   (manager?'<div class="panel"><h3>Invite a teammate</h3><p>Seats: '+(info.members.length+1)+' / '+info.limit+' (owner included). Pending invitations reserve seats.</p><form class="work-form" id="team-invite"><label>Email<input name="email" type="email" maxlength="160" required></label><label>Role<select name="role">'+opts('viewer')+'</select></label><button class="btn btn-accent">Create invitation</button></form><p class="billing-note">Invitees must sign in with the verified email address. Links expire after 72 hours.</p><div id="team-invite-result"></div></div>':'<div class="panel"><h3>Your role</h3><p>'+esc(role)+' access. Only workspace managers can administer invitations and members.</p></div>')+'</div>'+
   (manager?'<div class="panel section-spaced"><h3>Team members</h3><p>Owner · full access</p>'+info.members.map(m=>'<div class="panel section-spaced"><strong>'+esc(m.email)+'</strong> · '+esc(m.role)+(m.active?'':' · seat suspended')+((role==='owner'||m.role!=='admin')?'<label>Role<select data-member-role="'+Number(m.id)+'">'+opts(m.role)+'</select></label><button class="work-btn" data-member-save="'+Number(m.id)+'">Save role</button> <button class="work-btn" data-member-remove="'+Number(m.id)+'">Remove</button>':'')+'</div>').join('')+'<h3>Pending invitations</h3>'+(info.invites.map(i=>'<p>'+esc(i.email)+' · '+esc(i.role)+' · Expires '+esc(stamp(i.expires_at))+((role==='owner'||i.role!=='admin')?' <button class="work-btn" data-invite-revoke="'+Number(i.id)+'">Revoke</button>':'')+'</p>').join('')||'<p>No pending invitations.</p>')+'</div><div class="panel section-spaced"><h3>Recent audit activity</h3>'+ (log.map(a=>'<p>'+esc(stamp(a.created_at))+' · '+esc(a.actor||'Former account')+' · '+esc(a.action)+'</p>').join('')||'<p>No recorded activity.</p>')+'</div>':'');
  root.querySelector('#team-switch-button').onclick=()=>{const target=root.querySelector('#team-switch').value;location.assign('/?workspace='+encodeURIComponent(target)+'#teams');};
  const rename=root.querySelector('#team-name');
  if(rename){rename.elements.name.value=user.workspace_name||'';rename.onsubmit=async e=>{e.preventDefault();try{await api('/api/team/name',{method:'PUT',body:JSON.stringify({name:rename.elements.name.value})});await refreshUser();renderTeams();}catch(err){toast(err.message);}};}
  const invite=root.querySelector('#team-invite');
  if(invite)invite.onsubmit=async e=>{e.preventDefault();const b=invite.querySelector('button');b.disabled=true;try{const r=await api('/api/team/invite',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(invite)))});const result=root.querySelector('#team-invite-result');result.innerHTML='<p>'+ (r.email_queued?'Invitation email queued.':'Email unavailable; share the invitation manually.')+'</p><button class="work-btn" data-copy-invite>Copy invitation link</button> <button class="work-btn" data-reload-team>Refresh roster</button>';result.querySelector('[data-copy-invite]').onclick=async()=>{try{await navigator.clipboard.writeText(r.link);toast('Invitation copied');}catch(e){toast('Clipboard unavailable');}};result.querySelector('[data-reload-team]').onclick=()=>renderTeams();invite.reset();}catch(err){toast(err.message);}finally{b.disabled=false;}};
  root.querySelectorAll('[data-member-save]').forEach(b=>b.onclick=async()=>{try{const id=Number(b.dataset.memberSave);const selected=root.querySelector('[data-member-role="'+id+'"]').value;await api('/api/team/members/'+id,{method:'PATCH',body:JSON.stringify({role:selected})});renderTeams();}catch(e){toast(e.message);}});
  root.querySelectorAll('[data-member-remove]').forEach(b=>b.onclick=async()=>{if(!confirm('Remove this member’s workspace access?'))return;try{await api('/api/team/members/'+Number(b.dataset.memberRemove),{method:'DELETE'});renderTeams();}catch(e){toast(e.message);}});
  root.querySelectorAll('[data-invite-revoke]').forEach(b=>b.onclick=async()=>{try{await api('/api/team/invites/'+Number(b.dataset.inviteRevoke),{method:'DELETE'});renderTeams();}catch(e){toast(e.message);}});
 }catch(e){if(view==='teams')root.textContent=e.message;}
}
async function acceptWorkspaceInvitation(){
 const match=location.hash.match(/^#invite=([A-Za-z0-9_-]{43})$/);
 if(!match||!signedIn)return false;
 if(!confirm('Accept this BeepNado workspace invitation with your signed-in email address?'))return false;
 try{const r=await api('/api/team/accept',{method:'POST',body:JSON.stringify({token:match[1]})});location.replace('/?workspace='+Number(r.workspace_id)+'#teams');return true;}catch(e){toast(e.message);return false;}
}

async function showIncidentCollaboration(id){
 const d=document.createElement('dialog');d.className='bn-investigator';document.body.append(d);d.addEventListener('close',()=>d.remove(),{once:true});d.showModal();
 async function paint(){
  try{
   const data=await api('/api/team/incidents/'+id);
   const edit=['owner','admin','operator'].includes(user.workspace_role);
   d.innerHTML='<h2>Incident collaboration</h2><p>Assigned to: '+esc(data.assignee?.email||'Unassigned')+'</p>'+(edit?'<form class="work-form" data-assign><label>Assignee<select name="member"><option value="">Unassigned</option>'+data.choices.map(x=>'<option value="'+Number(x.id)+'" '+(x.id===data.assignee?.member_id?'selected':'')+'>'+esc(x.email)+'</option>').join('')+'</select></label><button class="work-btn">Assign</button></form><form class="work-form" data-note><label>Incident note<textarea name="body" maxlength="2000" required></textarea></label><button class="work-btn">Add note</button></form>':'')+'<h3>Notes</h3>'+(data.notes.map(n=>'<div class="panel section-spaced"><strong>'+esc(n.actor||'Former account')+'</strong><p>'+esc(fmt(new Date(n.created_at*1000).toISOString()))+'</p><p>'+esc(n.body)+'</p></div>').join('')||'<p>No notes yet.</p>')+'<button class="work-btn" data-close>Close</button>';
   d.querySelector('[data-close]').onclick=()=>d.close();
   const a=d.querySelector('[data-assign]');if(a)a.onsubmit=async e=>{e.preventDefault();try{await api('/api/team/incidents/'+id+'/assignment',{method:'PUT',body:JSON.stringify({member_id:a.elements.member.value?Number(a.elements.member.value):null})});paint();}catch(err){toast(err.message);}};
   const n=d.querySelector('[data-note]');if(n)n.onsubmit=async e=>{e.preventDefault();try{await api('/api/team/incidents/'+id+'/notes',{method:'POST',body:JSON.stringify({body:n.elements.body.value})});paint();}catch(err){toast(err.message);}};
  }catch(e){d.textContent=e.message;}
 }
 await paint();
}
async function editMonitorLabels(id){
 const m=dash.monitors.find(x=>x.id===id);if(!m)return;
 const d=document.createElement('dialog');d.className='bn-investigator';d.innerHTML='<h2>Monitor labels</h2><form class="work-form"><label>Client group<input name="group" maxlength="80"></label><label>Tags (comma separated, up to 10)<input name="tags" maxlength="410"></label><button class="work-btn">Save</button></form><button class="work-btn" data-close>Cancel</button>';
 const f=d.querySelector('form');f.elements.group.value=m.client_group||'';f.elements.tags.value=m.tags||'';f.onsubmit=async e=>{e.preventDefault();try{await api('/api/team/monitors/'+id+'/labels',{method:'PUT',body:JSON.stringify({client_group:f.elements.group.value,tags:f.elements.tags.value.split(',').map(x=>x.trim()).filter(Boolean)})});dash=await api('/api/dashboard');d.close();renderView();}catch(err){toast(err.message);}};d.querySelector('[data-close]').onclick=()=>d.close();document.body.append(d);d.addEventListener('close',()=>d.remove(),{once:true});d.showModal();
}
