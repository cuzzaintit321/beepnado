/* Interactive marketing layer. Every simulated state is explicitly fictitious. */
(() => {
'use strict';
const all=s=>Array.from(document.querySelectorAll(s));
const detail=[
['Uptime checks','Make real HTTP and HTTPS checks against public websites you are authorized to monitor. We record the latest response and history.'],
['Incident timeline','Two consecutive failures open an incident. A later successful check closes it and preserves the timeline.'],
['Shareable status pages','Create a public status page that shows selected services, current observed states and recent incidents.'],
['SSL certificate visibility','See certificate expiry observations during successful HTTPS checks. BeepNado does not renew certificates.'],
['Faster paid checks','One-minute checks are planned for configured paid subscriptions. Until billing is configured, only the free plan is active.'],
['Outage & recovery feed','View real incident and recovery notifications in the customer dashboard. Email requires configured SMTP; SMS is not yet offered.']
];
const plans=[
['Free — $0/month','Two monitors, primary-region checks approximately every five minutes, incidents and a public status page. Signup requires a verified email.'],
['Pro — proposed $12/month','Twenty monitors and one-minute primary-region checks, once Stripe subscriptions are configured, tested and activated.'],
['Team — proposed $39/month','One hundred monitors and one-minute primary-region checks, once Stripe subscriptions are configured, tested and activated.']
];
const dialog=document.createElement('dialog');dialog.className='beep-modal';
dialog.innerHTML='<h2></h2><p></p><button type="button">Close</button>';
document.body.appendChild(dialog);
let lastFocus;
const open=(item)=>{lastFocus=document.activeElement;dialog.querySelector('h2').textContent=item[0];dialog.querySelector('p').textContent=item[1];dialog.showModal()};
dialog.querySelector('button').onclick=()=>dialog.close();dialog.addEventListener('close',()=>lastFocus?.focus?.());
for(const [selector,items] of [['.feature-card',detail],['.price-card',plans]]){
 all(selector).forEach((el,i)=>{
  if(!items[i])return;
  el.classList.add('beep-active');el.tabIndex=0;el.setAttribute('role','button');
  el.setAttribute('aria-label','More about '+items[i][0]);
  const act=(event)=>{if(event?.target?.closest('button,a'))return;open(items[i])};
  el.addEventListener('click',act);
  el.addEventListener('keydown',e=>{if(e.target===el && (e.key==='Enter'||e.key===' ')){e.preventDefault();open(items[i])}});
 });
}
const demo=document.createElement('section');demo.className='beep-sim';demo.id='simulator';
demo.innerHTML='<div class="kicker">SIMULATION — NOT LIVE MONITOR DATA</div><h2>See how downtime gets detected.</h2><p>Choose a sample outcome. Real monitoring results appear only in your signed-in dashboard.</p><div role="group" aria-label="Simulated monitor states"><button type="button" data-beep="up" aria-pressed="true">● Online</button><button type="button" data-beep="down" aria-pressed="false">● Down</button><button type="button" data-beep="recovered" aria-pressed="false">● Recovered</button></div><div class="beep-sim-result" aria-live="polite"><strong id="sim-state">ONLINE</strong><p id="sim-note">Example: HTTP 200 · 218 ms · No open incident</p></div>';
document.getElementById('features')?.after(demo);
const states={up:['ONLINE','Example: HTTP 200 · 218 ms · No open incident'],down:['DOWN','Example: two failed checks · Confirmed outage opened'],recovered:['RECOVERED','Example: HTTP 200 · Service recovered · Incident closed']};
all('[data-beep]').forEach(b=>b.addEventListener('click',()=>{
 const d=states[b.dataset.beep];document.getElementById('sim-state').textContent=d[0];
 document.getElementById('sim-note').textContent=d[1];
 demo.querySelector('.beep-sim-result').classList.toggle('down',b.dataset.beep==='down');
 all('[data-beep]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));
}));
const faq=document.createElement('section');faq.className='beep-faq';faq.id='faq';
const qs=[
['Is the monitoring real?','Yes, after signup. The authenticated dashboard displays actual HTTP observations. The marketing demo is simulated.'],
['Can I use BeepNado for free?','The free tier supports two monitors. Public registration opens after verification email delivery has been configured and tested.'],
['Can I buy Pro or Team now?','Not yet. Paid tiers are proposed. Stripe billing must pass test payments and webhook verification first.'],
['Will BeepNado fix my website?','No. It detects and records downtime. Monitoring is not automated repair or a guaranteed uptime SLA.']
];
faq.innerHTML='<h2>Frequently asked questions</h2>';
for(const [question,answer] of qs){const d=document.createElement('details');const summary=document.createElement('summary');summary.textContent=question;const p=document.createElement('p');p.textContent=answer;d.append(summary,p);faq.appendChild(d)}
document.getElementById('pricing')?.after(faq);
})();
