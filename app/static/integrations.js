/* Notification destinations; never return or display saved webhook credentials. */
async function renderIntegrations(){
 const root=$('work-content');
 root.innerHTML='<div class="panel"><h3>Notification integrations</h3><p>Loading channels…</p></div>';
 try {
  const channels=await api('/api/integrations');
  if(view!=='integrations')return;
  root.innerHTML=`<div class="work-columns"><div class="panel"><h3>Connected channels</h3><p class="billing-note">Automatic incident notifications follow your Arm / Disarm preference. Test notifications are sent on request.</p>${channels.length?channels.map(c=>`<div class="panel section-spaced"><strong>${esc(c.label)}</strong> · ${esc(c.kind)}<p class="billing-note">Pending: ${Number(c.pending)} · Failed: ${Number(c.failed)} · Last delivery: ${c.last_delivered?fmt(new Date(c.last_delivered*1000).toISOString()):'None'}</p><button class="work-btn" data-channel-test="${Number(c.id)}">Send test</button> <button class="work-btn" data-channel-delete="${Number(c.id)}">Remove</button></div>`).join(''):empty('No channels connected','Connect Slack or Discord to receive incident updates.')}</div><div class="panel"><h3>Connect a channel</h3><form id="channel-form" class="work-form"><label>Provider<select name="kind"><option value="slack">Slack</option><option value="discord">Discord</option></select></label><label>Channel name<input name="label" required maxlength="60" placeholder="Operations alerts"></label><label>Provider webhook URL<input name="endpoint" type="password" required maxlength="500" autocomplete="off" placeholder="https://…"></label><button type="submit" class="btn btn-accent">Connect channel</button></form><p class="billing-note">Create an incoming webhook in your Slack or Discord settings, then paste its URL here. The URL is a secret and is not returned by the API.</p></div></div>`;
  root.querySelector('#channel-form').onsubmit=async e=>{
   e.preventDefault();const form=e.currentTarget;const data=Object.fromEntries(new FormData(form));const button=form.querySelector('button');button.disabled=true;
   try{await api('/api/integrations',{method:'POST',body:JSON.stringify(data)});toast('Channel connected');renderIntegrations();}
   catch(err){toast(err.message);button.disabled=false;}
  };
  root.querySelectorAll('[data-channel-test]').forEach(b=>b.onclick=async()=>{try{await api('/api/integrations/'+b.dataset.channelTest+'/test',{method:'POST'});toast('Test queued; check your channel shortly.');renderIntegrations();}catch(e){toast(e.message);}});
  root.querySelectorAll('[data-channel-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Remove this notification channel and its queued messages?'))return;try{await api('/api/integrations/'+b.dataset.channelDelete,{method:'DELETE'});toast('Channel removed');renderIntegrations();}catch(e){toast(e.message);}});
 }catch(e){if(view==='integrations')root.textContent=e.message;}
}
