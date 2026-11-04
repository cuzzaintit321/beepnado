/* BeepNado dark monitoring console. Everything shown comes from /api/dashboard. */
(() => {
  'use strict';
  const filterState = { query: '', status: 'all' };
  const getMonitors = () => dash?.monitors || [];
  const getChecks = () => dash?.checks || [];
  const num = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const tidy = (v) => esc(String(v ?? ''));

  const statusOf = m => m.display_state || m.state || 'pending';
  const monitorTypes = {
    http: 'Website', api: 'JSON API', dns: 'DNS record', ssl: 'SSL certificate',
    domain: 'Domain expiry', ping: 'ICMP ping', tcp: 'TCP port', udp: 'UDP reply', heartbeat: 'Heartbeat'
  };

  function counts() {
    const monitors = getMonitors();
    return {
      total: monitors.length,
      up: monitors.filter(m => statusOf(m) === 'up').length,
      down: monitors.filter(m => statusOf(m) === 'down').length,
      degraded: monitors.filter(m => statusOf(m) === 'degraded').length,
      pending: monitors.filter(m => statusOf(m) === 'pending').length,
      maintenance: monitors.filter(m => statusOf(m) === 'maintenance').length
    };
  }

  const localRegions = new Set(['primary', 'maintenance', 'heartbeat']);
  const isExternal = check => !localRegions.has(check.region);

  function externalSummary(monitor) {
    const observations = monitor.regional_checks || [];
    const fresh = observations.filter(check => !check.stale);
    if (!fresh.length) {
      const label = observations.length ? 'Public check stale' : 'No recent public check';
      return `<small class="bn-public-check bn-public-stale">${label}</small>`;
    }
    const states = new Set(fresh.map(check => check.state));
    const labels = {up: 'Public check passed', down: 'Public check failed', degraded: 'Public check inconclusive'};
    const label = states.size === 1 ? labels[fresh[0].state] : 'Public checks disagree';
    const title = fresh.map(check => check.region + ' · ' + fmt(check.checked_at)).join(' / ');
    return `<small class="bn-public-check bn-public-${states.size === 1 && states.has('up') ? 'ok' : 'warning'}" title="${tidy(title)}">${label}</small>`;
  }

  function recentBars(m) {
    const checks = getChecks().filter(c => Number(c.monitor_id) === Number(m.id)).slice(0, 24).reverse();
    const gaps = Math.max(0, 24 - checks.length);
    const maintenance = checks.filter(c => c.region === 'maintenance').length;
    const external = checks.filter(isExternal).length;
    const bars = '<span class="bn-tick bn-empty" aria-hidden="true"></span>'.repeat(gaps) +
      checks.map(c => `<span class="bn-tick bn-${['up','down','degraded'].includes(c.state) ? c.state : 'empty'}${c.region === 'maintenance' ? ' bn-planned-tick' : isExternal(c) ? ' bn-public-tick' : ''}" title="${tidy(fmt(c.checked_at))}: ${tidy(prettyState(c.state))}${c.region === 'maintenance' ? ' · planned maintenance' : isExternal(c) ? ' · public ' + tidy(c.region) : ''}" aria-hidden="true"></span>`).join('');
    return `<div class="bn-history"><div class="bn-history-bars" role="img" aria-label="${checks.length} recent recorded checks for ${tidy(m.name)}${maintenance ? ', ' + maintenance + ' during maintenance' : ''}${external ? ', ' + external + ' public' : ''}">${bars}</div><span class="bn-history-caption">${checks.length ? checks.length + ' recent checks' : 'No checks yet'}${maintenance ? ' · ' + maintenance + ' planned' : ''}${external ? ' · ' + external + ' public' : ''}</span></div>`;
  }

  function monitorItem(m) {
    const state = statusOf(m);
    const window = m.maintenance;
    const notice = window ? (state === 'maintenance' ? 'Ends ' + fmt(window.ends_at) : 'Planned ' + fmt(window.starts_at)) : '';
    const id = num(m.id);
    const heartbeat = m.kind === 'heartbeat';
    const port = ['tcp', 'udp', 'ssl'].includes(m.kind) ? m.probe_config?.port : null;
    const target = heartbeat ? 'Heartbeat · every ' + m.heartbeat_interval_s / 60 + ' min'
      : m.kind === 'http' ? m.url : monitorTypes[m.kind] + ' · ' + m.url + (port ? ':' + port : '');
    const timing = heartbeat
      ? (m.heartbeat_last_at == null ? 'Awaiting first ping' : 'Last ping ' + fmt(new Date(m.heartbeat_last_at * 1000).toISOString()))
      : (m.maintenance_last_checked ? 'Maintenance check ' + fmt(m.maintenance_last_checked)
         : m.last_checked ? 'Primary check ' + fmt(m.last_checked) : 'Awaiting first check');
    const pct = m.samples_30d ? `${num(m.success_rate_30d).toFixed(2)}%` : '—';
    const rules = [m.required_phrase && 'Required phrase', m.expected_status && 'HTTP ' + m.expected_status,
      m.max_response_ms && '≤ ' + m.max_response_ms + ' ms'].filter(Boolean).join(' · ');
    return `<article class="bn-monitor-row" aria-label="${tidy(m.name)}: ${tidy(prettyState(state))}">
      <div class="bn-monitor-identity"><span class="bn-state-dot bn-${tidy(state)}" aria-hidden="true"></span>
        <div class="bn-monitor-title"><strong>${tidy(m.name)}</strong><span class="bn-host" title="${tidy(target)}">${tidy(target)}</span>${rules ? '<span class="bn-host">' + tidy(rules) + '</span>' : ''}</div></div>
      <div class="bn-monitor-meta"><span>${pill(state)}</span><small>${tidy(timing)}</small>${notice ? '<small class="bn-maintenance-text">' + tidy(notice) + '</small>' : ''}
        ${m.detail ? '<small class="bn-check-detail" title="' + tidy(m.detail) + '">' + tidy(m.detail) + '</small>' : ''}
        ${heartbeat ? '' : externalSummary(m)}</div>
      ${recentBars(m)}
      <div class="bn-monitor-metrics">${heartbeat
        ? '<strong>' + num(m.heartbeat_grace_s) / 60 + ' min</strong><small>Grace period</small>'
        : '<strong>' + pct + '</strong><small>30d checks passed</small><span>' + (m.response_ms == null ? '—' : num(m.response_ms) + ' ms') + ' latest</span>'}</div>
      <div class="bn-monitor-actions">${heartbeat ? '<button class="bn-btn bn-btn-small" data-heartbeat-token="' + id + '">Replace key</button>' : ''}
        ${m.kind === 'http' ? '<button class="bn-btn bn-btn-small" data-check-rules="' + id + '" aria-label="Check rules for ' + tidy(m.name) + '">Rules</button>' : ''}
        ${heartbeat ? '' : '<button class="bn-btn bn-btn-small" data-maintenance="' + id + '">Maintenance</button>'}<button class="bn-btn bn-btn-small bn-investigate-button" data-investigate="${id}" aria-label="Investigate ${tidy(m.name)}">Investigate ↗</button>${heartbeat ? '' : '<button class="bn-btn bn-btn-small" data-check="' + id + '" aria-label="Check ' + tidy(m.name) + ' now">Check now</button>'}<button class="bn-icon-btn" data-remove="${id}" aria-label="Remove ${tidy(m.name)}" title="Remove monitor">×</button></div>
    </article>`;
  }


  // Incident Investigator: evidence and possible causes only, never invented facts.
  const investigatorDialog=document.createElement('dialog');
  investigatorDialog.className='bn-investigator-dialog';
  document.body.appendChild(investigatorDialog);
  let copyableReport='';
  const protocolGuidance = {
    api: 'Check the endpoint status and the configured JSON pointer and value.',
    dns: 'Compare the public DNS answer with the record at your authoritative DNS provider.',
    ssl: 'Check the hostname, certificate chain, and renewal date.',
    domain: 'Confirm the registration expiry directly with your registrar.',
    ping: 'Check whether the host and its firewall respond to ICMP echo requests.',
    tcp: 'Check that the service is listening and the port is reachable from this network.',
    udp: 'Check that the service replies to the configured request and that the reply prefix is correct.'
  };

  function investigatorHint(c, kind) {
    if (kind === 'heartbeat') {
      if (!c) return ['Awaiting first heartbeat','No check-in has been recorded.','Run the check-in command after the job succeeds.'];
      if (c.state==='up') return ['Heartbeat received','The job sent a check-in.','Review the schedule and recent check-ins.'];
      return ['Missed heartbeat',c.error,'Check the job logs, the schedule and whether its check-in command ran.'];
    }
    if (!c) return ['No observations yet','The monitor has not recorded a check.','Run a check to collect evidence.'];
    const err=String(c.error||''), code=Number(c.http_code||0);
    if (err.includes('public reference host')) {
      return ['Monitoring connection unverified', c.detail || err, 'Compare with an off-site probe before calling this an outage.'];
    }
    if (protocolGuidance[kind]) {
      const title = c.state === 'up' ? monitorTypes[kind] + ' check passed' : monitorTypes[kind] + ' evidence';
      return [title, [c.detail, err].filter(Boolean).join('. ') || (code ? 'HTTP ' + code : 'Check completed.'),
              protocolGuidance[kind]];
    }
    if (/gaierror|name or service not known|dns|name resolution/i.test(err)) return ['DNS resolution','The hostname did not resolve during this check.','Check authoritative DNS and propagation.'];
    if (/certificate verify failed|certificate has expired|SSLCertVerificationError/i.test(err)) return ['TLS certificate','The HTTPS certificate could not be validated.','Check the certificate, hostname and full chain.'];
    if (/ssl|tls|handshake/i.test(err)) return ['HTTPS handshake','An HTTPS connection could not complete.','Inspect reverse-proxy TLS configuration and logs.'];
    if (/ConnectionRefusedError|connection refused/i.test(err)) return ['Connection refused','The network endpoint refused the connection; it may still work externally.','Check the listening port, firewall and router NAT loopback.'];
    if (/timed out|timeout/i.test(err)) return ['Timeout','The check exceeded its timeout.','Inspect network connectivity and server load.'];
    if (/required phrase|response content|content check|character encoding/i.test(err)) return ['Content check',err,'Open the monitored URL and verify the required phrase appears in the response source.'];
    if (/^Expected HTTP /i.test(err)) return ['HTTP status mismatch',err,'Check the endpoint or update the expected status.'];
    if (/^Response too slow:/i.test(err)) return ['Response time exceeded',err,'Review server response times and the configured limit.'];
    if (code>=500) return ['Server-side HTTP '+code,'The endpoint returned a server error.','Check the application and upstream error logs.'];
    if (code===401||code===403) return ['Access restriction','The endpoint returned HTTP '+code+'.','Review authentication and WAF rules.'];
    if (code===404) return ['Missing monitored path','The endpoint returned HTTP 404. Other pages may still work.','Review the path and deployment routes.'];
    if (code>=400) return ['HTTP '+code,'The endpoint returned a non-success response.','Inspect the application logs and requested path.'];
    if (c.state==='up') return ['Currently responding','The latest recorded HTTP response succeeded.','Review history for intermittent failures.'];
    return ['Unclassified failure',err||'A check failed, without enough evidence to identify the cause.','Check server logs and verify from an external network.'];
  }
  function describeEvidence(check) {
    if (!check) return 'No saved observation for this event.';
    return [fmt(check.checked_at), prettyState(check.state), check.http_code == null ? '' : 'HTTP ' + check.http_code,
      check.response_ms == null ? '' : check.response_ms + ' ms', check.detail, check.error].filter(Boolean).join(' · ');
  }

  function investigateMonitor(m, incident, observations) {
    const checks=observations.filter(c=>Number(c.monitor_id)===Number(m.id)).slice(0,10);
    const heartbeat=m.kind==='heartbeat';
    const region=heartbeat?'heartbeat':'primary';
    const maintenanceCheck = checks.find(c=>c.region==='maintenance' && c.checked_at===m.maintenance_last_checked);
    const latest = maintenanceCheck || (m.last_checked ? (checks.find(c=>c.region===region && c.checked_at===m.last_checked) || m) : null);
    const opening = incident?.opening_evidence || (incident && checks.find(c=>c.region===region && c.checked_at===incident.opened_at && c.state==='down'));
    const recovery = incident?.recovery_evidence || (incident?.resolved_at && checks.find(c=>c.region===region && c.checked_at===incident.resolved_at && c.state==='up'));
    const evidence = incident ? opening : latest;
    const hint=investigatorHint(evidence, m.kind);
    let safeUrl=heartbeat?'Heartbeat':String(m.url||'').split('?')[0];
    try { const u=new URL(m.url); safeUrl=u.origin+u.pathname; } catch(e){}
    const observed=evidence?(evidence.checked_at||m.last_checked):null;
    const lines=checks.map(c=>c.region+' · '+describeEvidence(c));
    const external = (m.regional_checks || []).map(c => {
      return c.region + (c.stale ? ' (stale)' : '') + ' · ' + describeEvidence(c);
    });
    copyableReport=[
      'BEEPNADO INCIDENT INVESTIGATOR · Evidence-based report',
      'Monitor: '+m.name+' · '+safeUrl,
      ...(m.samples_30d ? ['30-day primary checks: '+m.samples_30d+' recorded · '+m.success_rate_30d+'% passed.'] : []),
      ...(incident ? ['Incident opened: '+fmt(incident.opened_at), 'Opening check: '+describeEvidence(opening),
        'Recovery check: '+(incident.resolved_at ? describeEvidence(recovery) : 'Incident is open.')] : [
        'Latest state: '+(latest?prettyState(latest.state):'Not checked')+(maintenanceCheck?' (during planned maintenance)':''),
        'Last observed: '+(observed?fmt(observed):'No check yet')]),
      'Possible issue: '+hint[0],
      'Observed evidence: '+hint[1],
      'Suggested next step: '+hint[2],
      'Recorded error: '+String(evidence?.error||'None').replace(/[\r\n]/g,' ').slice(0,120),
      ...(heartbeat ? [] : ['Public probes:', ...(external.length ? external : ['No recent public observation.'])]),
      'Recent checks:',...(lines.length?lines:['No observations recorded.']),
      'Interpretation is not a confirmed root cause. Same-host checks cannot detect whole-server or Internet outages.'
    ].join('\n');
    const history=checks.length?checks.map(c=>
      '<div class="bn-investigation-row"><span>'+tidy(fmt(c.checked_at))+'</span><span>'+tidy(prettyState(c.state))+(c.region==='maintenance'?' · planned':isExternal(c)?' · '+tidy(c.region):'')+'</span>'+'<span>'+tidy(c.http_code==null ? c.detail || c.error || '—' : 'HTTP '+c.http_code)+'</span><span>'+tidy(c.response_ms==null?'—':c.response_ms+' ms')+'</span></div>').join(''):'<p>No check history yet.</p>';
    investigatorDialog.innerHTML=
      '<div class="bn-investigator-top"><span>REAL CHECK EVIDENCE · BEEPNADO</span><button type="button" data-investigator-close aria-label="Close">×</button></div>'+
      '<h2>Incident Investigator</h2><p>'+tidy(m.name)+' · '+tidy(safeUrl)+'</p>'+
      (incident ? '<h3>Incident evidence</h3><p><strong>Opening check</strong> · '+tidy(describeEvidence(opening))+
        '</p><p><strong>Recovery check</strong> · '+tidy(incident.resolved_at ? describeEvidence(recovery) : 'Incident is open.')+'</p>' : '')+
      '<div class="bn-investigator-cause"><small>POSSIBLE ISSUE — NOT A CONFIRMED ROOT CAUSE</small><h3>'+tidy(hint[0])+'</h3><p>'+tidy(hint[1])+'</p><strong>What to check next</strong><p>'+tidy(hint[2])+'</p></div>'+
      (heartbeat ? '' : '<h3>Public probes</h3>' + (external.length
        ? '<ul class="bn-public-evidence">' + external.map(line => '<li>' + tidy(line) + '</li>').join('') + '</ul>'
        : '<p>No recent public observation.</p>')) +
      '<h3>Recent recorded checks</h3><div class="bn-investigator-history">'+history+'</div>'+
      '<p class="bn-investigator-note">Based only on recorded observations; no simulated data. If monitoring from the same server, a total server or network outage will not be detected independently.</p>'+
      '<div class="bn-investigator-actions"><button type="button" class="bn-btn bn-btn-primary" data-investigator-copy>Copy client report</button><button type="button" class="bn-btn bn-btn-quiet" data-investigator-close>Close</button></div>';
    investigatorDialog.showModal();
  }
  investigatorDialog.addEventListener('click',async e=>{
    const button=e.target.closest('button');
    if(!button) return;
    if(button.hasAttribute('data-investigator-close')) investigatorDialog.close();
    if(button.hasAttribute('data-investigator-copy')){
      try {await navigator.clipboard.writeText(copyableReport);toast('Report copied to clipboard');}
      catch(e){toast('Clipboard unavailable; copy report from the investigation panel.');}
    }
  });

  function filteredMonitors() {
    return getMonitors().filter(m => {
      const phrase = (String(m.name || '') + ' ' + String(m.url || '')).toLowerCase();
      const wanted = filterState.status;
      const status = statusOf(m);
      return phrase.includes(filterState.query.toLowerCase()) && (wanted === 'all' || status === wanted);
    });
  }

  function emptyList() {
    return '<div class="bn-empty-state"><div class="bn-empty-icon">◉</div><strong>No matching monitors</strong><p>Try another search or status filter, or add a monitor.</p></div>';
  }

  function sideSnapshot() {
    const n = counts();
    const all = getMonitors();
    const samples = all.reduce((sum, m) => sum + num(m.samples_30d), 0);
    const passed = all.reduce((sum, m) => sum + num(m.samples_30d) * num(m.success_rate_30d) / 100, 0);
    const percent = samples ? (passed / samples * 100).toFixed(2) + '%' : '—';
    return `<section class="bn-panel bn-snapshot" aria-label="Current monitor status"><h3>Current status <span class="bn-live-dot" title="Latest observations, not live network status"></span></h3>
      <div class="bn-status-trio"><div><strong class="bn-color-down">${n.down}</strong><small>Down</small></div><div><strong class="bn-color-up">${n.up}</strong><small>Up</small></div><div><strong class="bn-color-pending">${n.degraded + n.pending}</strong><small>Pending</small></div></div>
      ${n.maintenance ? '<p class="bn-maintenance-text">' + n.maintenance + ' in planned maintenance</p>' : ''}
      <div class="bn-snapshot-footer">Monitoring <strong>${n.total}</strong> of <strong>${num(dash?.limit)}</strong> allowed monitors</div></section>
      <section class="bn-panel bn-facts"><h3>Check history</h3><div class="bn-fact"><span>30-day check success rate</span><strong>${percent}</strong></div><div class="bn-fact"><span>30-day primary checks</span><strong>${samples.toLocaleString()}</strong></div><div class="bn-fact"><span>Recent incident records</span><strong>${(dash?.incidents || []).length}${(dash?.incidents || []).length === 30 ? '+' : ''}</strong></div><p>Check success rates exclude planned maintenance. They measure recorded checks.</p></section>`;
  }

  function ruleFields(m={}, disabled=false) {
    const off=disabled ? 'disabled' : '';
    return `<label>Required phrase <small>(optional)</small><input name="required_phrase" maxlength="120" value="${tidy(m.required_phrase)}" placeholder="e.g. Service operational" ${off}></label>
      <label>Expected HTTP status <small>(optional)</small><input name="expected_status" type="number" min="200" max="599" step="1" value="${tidy(m.expected_status)}" placeholder="e.g. 200" ${off}></label>
      <label>Maximum response time in ms <small>(optional)</small><input name="max_response_ms" type="number" min="100" max="15000" step="1" value="${tidy(m.max_response_ms)}" placeholder="e.g. 1500" ${off}></label>`;
  }

  function ruleData(fd) {
    return {
      required_phrase: String(fd.get('required_phrase') || '').trim(),
      expected_status: Number(fd.get('expected_status')) || null,
      max_response_ms: Number(fd.get('max_response_ms')) || null
    };
  }

  const rulesDialog=document.createElement('dialog');
  rulesDialog.className='bn-investigator-dialog bn-rules-dialog';
  rulesDialog.setAttribute('aria-labelledby','bn-rules-title');
  document.body.appendChild(rulesDialog);

  function editRules(m) {
    rulesDialog.innerHTML=`<div class="bn-investigator-top"><span>MONITOR SETTINGS</span><button type="button" data-close-rules aria-label="Close">×</button></div>
      <h2 id="bn-rules-title">Check rules</h2><p>${tidy(m.name)}</p>
      <form class="work-form">${ruleFields(m)}
        <p class="bn-note">Phrase checks look for case-insensitive text in the first 256 KiB of the HTTP response. Response time is measured through the response headers.</p>
        <p class="bn-note" role="status"></p>
        <div class="bn-investigator-actions"><button type="submit" class="bn-btn bn-btn-primary">Save rules</button><button type="button" class="bn-btn bn-btn-quiet" data-close-rules>Cancel</button></div>
      </form>`;
    rulesDialog.querySelectorAll('[data-close-rules]').forEach(button=>button.addEventListener('click',()=>rulesDialog.close()));
    const form=rulesDialog.querySelector('form');
    form.addEventListener('submit',async e=>{
      e.preventDefault();
      const button=form.querySelector('[type="submit"]');
      button.disabled=true;
      form.querySelector('[role="status"]').textContent='';
      try {
        await api('/api/monitors/'+num(m.id)+'/rules',{method:'PATCH',body:JSON.stringify(ruleData(new FormData(form)))});
      } catch (error) {
        form.querySelector('[role="status"]').textContent=error.message;
        button.disabled=false;
        return;
      }
      rulesDialog.close();
      toast('Check rules saved');
      try { dash=await api('/api/dashboard'); renderView(); }
      catch (error) { toast(error.message); }
    });
    rulesDialog.showModal();
  }

  const maintenanceDialog = document.createElement('dialog');
  maintenanceDialog.className = 'bn-investigator-dialog bn-rules-dialog';
  maintenanceDialog.setAttribute('aria-labelledby', 'bn-maintenance-title');
  document.body.appendChild(maintenanceDialog);

  function localTimeInput(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function manageMaintenance(monitor) {
    const window = monitor.maintenance;
    const active = statusOf(monitor) === 'maintenance';
    const action = active ? 'End maintenance' : 'Cancel scheduled window';
    maintenanceDialog.innerHTML = `
      <div class="bn-investigator-top"><span>MONITOR SETTINGS</span><button type="button" data-close-maintenance aria-label="Close">×</button></div>
      <h2 id="bn-maintenance-title">Planned maintenance</h2><p>${tidy(monitor.name)}</p>
      <p>BeepNado keeps checking your site. During the window, results stay in history and outage and recovery alerts are suppressed. Your public status page shows the window.</p>
      <form class="work-form">
        ${window ? `<p>${active ? 'In progress' : 'Scheduled'} · ${tidy(fmt(window.starts_at))} to ${tidy(fmt(window.ends_at))}</p>`
          : `<label>Starts <small>(leave blank to start now)</small><input name="starts_at" type="datetime-local"></label>
             <label>Ends<input name="ends_at" type="datetime-local" value="${localTimeInput(new Date(Date.now() + 3600000))}" required></label>
             <p class="bn-note">Times are in your browser’s time zone.</p>`}
        <p class="bn-note" role="status"></p>
        <div class="bn-investigator-actions"><button type="submit" class="bn-btn bn-btn-primary">${window ? action : 'Save window'}</button><button type="button" class="bn-btn bn-btn-quiet" data-close-maintenance>Close</button></div>
      </form>`;
    maintenanceDialog.querySelectorAll('[data-close-maintenance]').forEach(button=>button.addEventListener('click',()=>maintenanceDialog.close()));
    const form = maintenanceDialog.querySelector('form');
    if (!window) {
      const start = form.querySelector('[name=starts_at]');
      const end = form.querySelector('[name=ends_at]');
      start.addEventListener('change', () => {
        if (start.value && end.value <= start.value) end.value = localTimeInput(new Date(new Date(start.value).getTime() + 3600000));
      });
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('[type=submit]');
      button.disabled = true;
      const feedback = form.querySelector('[role=status]');
      feedback.textContent = '';
      const path = '/api/monitors/' + num(monitor.id) + '/maintenance';
      try {
        if (window) {
          await api(path + '/' + num(window.id), {method:'DELETE'});
        } else {
          const fields = new FormData(form);
          const starts = fields.get('starts_at');
          await api(path, {method:'POST', body:JSON.stringify({
            starts_at: starts ? new Date(starts).toISOString() : null,
            ends_at: new Date(fields.get('ends_at')).toISOString()
          })});
        }
      } catch (error) {
        feedback.textContent = error.message;
        button.disabled = false;
        return;
      }
      maintenanceDialog.close();
      toast(window ? 'Maintenance window closed' : 'Maintenance window saved');
      try { dash = await api('/api/dashboard'); renderView(); }
      catch (error) { toast(error.message); }
    });
    maintenanceDialog.showModal();
  }

  const heartbeatDialog=document.createElement('dialog');
  heartbeatDialog.className='bn-investigator-dialog bn-heartbeat-dialog';
  heartbeatDialog.setAttribute('aria-labelledby','bn-heartbeat-title');
  document.body.appendChild(heartbeatDialog);
  heartbeatDialog.addEventListener('close',()=>heartbeatDialog.replaceChildren());

  function showHeartbeatCommand(token) {
    const command = "curl --fail --silent --show-error --max-time 15 --request POST --header 'Authorization: Bearer " + token + "' '" + location.origin + "/api/heartbeat'";
    heartbeatDialog.innerHTML='<h2 id="bn-heartbeat-title">Set up your heartbeat</h2><p>Save this private command now. Run it after your job completes successfully.</p>' +
      '<pre class="bn-heartbeat-command"></pre><form method="dialog" class="bn-investigator-actions"><button type="button" class="bn-btn bn-btn-primary" data-copy-heartbeat>Copy command</button><button class="bn-btn bn-btn-quiet">Done</button></form>';
    heartbeatDialog.querySelector('pre').textContent=command;
    heartbeatDialog.querySelector('[data-copy-heartbeat]').addEventListener('click',async ()=>{
      try { await navigator.clipboard.writeText(command); toast('Heartbeat command copied'); }
      catch { toast('Select and copy the command from the dialog'); }
    });
    heartbeatDialog.showModal();
  }

  const networkFields = {
    api: `<label>Expected HTTP status<input name="status" type="number" min="200" max="299" value="200" required></label>
      <label>JSON pointer <small>(optional)</small><input name="json_path" maxlength="200" placeholder="/status"></label>
      <label>Expected JSON value <small>(optional)</small><input name="expected" maxlength="512" placeholder="&quot;ok&quot;"></label>
      <p class="bn-note">GET request. The response must contain valid JSON. Use a pointer such as /items/0/name to check a value.</p>`,
    dns: `<label>Record type<select name="record_type">${['A','AAAA','CNAME','MX','NS','TXT','CAA'].map(t=>'<option>'+t+'</option>').join('')}</select></label>
      <label>Expected record value <small>(optional)</small><input name="expected" maxlength="512"></label>
      <p class="bn-note">Uses Google Public DNS. Leave the value blank to check for any answer of the selected type.</p>`,
    ssl: `<label>Minimum days until expiry<input name="days" type="number" min="1" max="365" value="14" required></label>
      <p class="bn-note">Validates the certificate and hostname on HTTPS port 443.</p>`,
    domain: `<label>Minimum days until expiry<input name="days" type="number" min="1" max="365" value="14" required></label>
      <p class="bn-note">Enter the registered domain, such as example.com. The registry needs to publish an expiry date.</p>`,
    ping: '<p class="bn-note">Sends an ICMP echo request. Some healthy services block ping.</p>',
    tcp: '<label>TCP port<input name="port" type="number" min="1" max="65535" required></label>',
    udp: `<label>UDP port<input name="port" type="number" min="1" max="65535" required></label>
      <label>Request bytes in hex<input name="payload" maxlength="384" placeholder="01020304" required></label>
      <label>Expected reply prefix in hex <small>(optional)</small><input name="response_prefix" maxlength="384" placeholder="01"></label>
      <p class="bn-note">Use a request your service will answer. A reply is required for a pass.</p>`
  };

  function addForm() {
    const full = getMonitors().length >= num(dash?.limit);
    const off = full ? 'disabled' : '';
    return `<section class="bn-panel bn-add-panel" id="bn-add-panel"><div class="bn-panel-title"><h3>Add a monitor</h3><span>+ New</span></div>
      <form class="work-form" id="monitor-form">
        <label>Monitor type<select name="kind" ${off}>${Object.entries(monitorTypes).map(([kind,label])=>`<option value="${kind}">${label}</option>`).join('')}</select></label>
        <label>Monitor name<input id="bn-monitor-name" name="name" placeholder="My business website" minlength="2" maxlength="100" required ${off}></label>
        <fieldset class="bn-monitor-fields" data-monitor-fields="http" ${off}>
          <label>Website URL<input name="url" type="url" placeholder="https://example.com" required maxlength="1500"></label>
          <details><summary>Optional check rules</summary>${ruleFields()}</details>
          <label class="bn-consent"><input type="checkbox" name="authorized" required> I own or am authorized to monitor this site.</label>
        </fieldset>
        <fieldset class="bn-monitor-fields" data-monitor-fields="heartbeat" hidden disabled>
          <label>Expected interval in minutes<input name="interval_minutes" type="number" min="1" max="43200" value="60" required></label>
          <label>Grace period in minutes<input name="grace_minutes" type="number" min="0" max="1440" value="5" required></label>
          <p class="bn-note">If your job misses its interval plus the grace period, BeepNado opens an incident and queues an email alert when email is configured.</p>
        </fieldset>
        ${Object.entries(networkFields).map(([kind,fields])=>`
          <fieldset class="bn-monitor-fields" data-monitor-fields="${kind}" hidden disabled>
            <label>${kind === 'api' ? 'API endpoint URL' : 'Public hostname'}<input name="target"
              type="${kind === 'api' ? 'url' : 'text'}" maxlength="${kind === 'api' ? 1500 : 253}"
              placeholder="${kind === 'api' ? 'https://example.com/health' : 'example.com'}" required></label>
            ${fields}
            <label class="bn-consent"><input type="checkbox" name="authorized" required> I own or am authorized to monitor this target.</label>
          </fieldset>`).join('')}
        <button class="bn-btn bn-btn-primary bn-btn-wide" type="submit" ${off}>+ Add monitor</button>
      </form><p class="bn-note">${full ? 'Plan limit reached. Remove a monitor before adding another.' : 'All monitor types share your plan limit.'}</p></section>`;
  }

  function summaryStrip() {
    const n = counts();
    const connectionNotice = dash?.primary_connectivity_unverified
      ? '<p class="bn-network-notice" role="status">Primary connection unverified. Network failures are marked inconclusive while public connectivity cannot be verified.</p>' : '';
    return `${connectionNotice}<div class="bn-summary-strip" aria-label="Monitor totals">
      <div><span>Monitors</span><strong>${n.total}<small> / ${num(dash?.limit)}</small></strong></div>
      <div><span>Operational</span><strong class="bn-color-up">${n.up}</strong></div>
      <div><span>Confirmed down</span><strong class="bn-color-down">${n.down}</strong></div>
      <div><span>Pending / degraded</span><strong class="bn-color-pending">${n.pending + n.degraded}</strong>${n.maintenance ? '<small class="bn-maintenance-text">' + n.maintenance + ' in maintenance</small>' : ''}</div>
    </div>`;
  }

  function monitorToolbar() {
    return `<div class="bn-toolbar"><div><h3>Monitors<span class="bn-count">${getMonitors().length}</span></h3><p>Websites, APIs, network services, and job heartbeats</p></div>
      <div class="bn-toolbar-actions"><button class="bn-btn bn-btn-quiet" data-bn-refresh>↻ Refresh</button><button class="bn-btn bn-btn-primary" data-bn-new>+ New monitor</button></div></div>
      <div class="bn-filter-bar"><label class="bn-search-label" for="bn-search">Search monitors<input id="bn-search" type="search" placeholder="Search by name or URL" autocomplete="off" value="${tidy(filterState.query)}"></label>
      <label class="bn-filter-label" for="bn-filter">Status<select id="bn-filter"><option value="all" ${filterState.status==='all'?'selected':''}>All statuses</option><option value="up" ${filterState.status==='up'?'selected':''}>Up</option><option value="down" ${filterState.status==='down'?'selected':''}>Down</option><option value="degraded" ${filterState.status==='degraded'?'selected':''}>Degraded</option><option value="pending" ${filterState.status==='pending'?'selected':''}>Pending</option><option value="maintenance" ${filterState.status==='maintenance'?'selected':''}>Maintenance</option></select></label></div>`;
  }

  function paintMonitorResults() {
    const target = document.getElementById('bn-monitor-results');
    if (!target) return;
    const list = filteredMonitors();
    target.innerHTML = list.length ? list.map(monitorItem).join('') : emptyList();
    const label = document.getElementById('bn-results-count');
    if (label) label.textContent = `${list.length} of ${getMonitors().length} monitors`;
  }

  window.monitors = function monitorsConsole() {
    const list = filteredMonitors();
    $('work-content').innerHTML = `${summaryStrip()}<div class="bn-layout"><div class="bn-main-column"><section class="bn-panel bn-monitor-panel">${monitorToolbar()}
        <div class="bn-list-heading"><span>MONITOR</span><span>STATUS</span><span>RECENT CHECKS</span><span>SUCCESS / LATENCY</span><span>ACTIONS</span></div>
        <div id="bn-monitor-results">${list.length ? list.map(monitorItem).join('') : emptyList()}</div>
        <div class="bn-list-footer" id="bn-results-count">${list.length} of ${getMonitors().length} monitors</div></section>
        <p class="bn-disclosure">Monitoring results reflect checks from this server's configured region. Same-host checks do not detect home Internet or server-wide outages.</p></div>
        <aside class="bn-aside">${sideSnapshot()}${addForm()}</aside></div>`;
    const form = document.getElementById('monitor-form');
    form?.querySelector('[name=kind]').addEventListener('change', e => {
      const kind=e.target.value;
      for (const fields of form.querySelectorAll('[data-monitor-fields]')) {
        fields.hidden = fields.disabled = fields.dataset.monitorFields !== kind;
      }
      form.querySelector('[name=name]').placeholder = kind === 'heartbeat' ? 'Nightly database backup' : 'My business website';
    });
    form?.addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(form);
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      try {
        if (fd.get('kind') === 'heartbeat') {
          const {token} = await api('/api/monitors/heartbeat', {method:'POST',body:JSON.stringify({
            name:fd.get('name'), interval_minutes:Number(fd.get('interval_minutes')), grace_minutes:Number(fd.get('grace_minutes'))
          })});
          showHeartbeatCommand(token);
          toast('Heartbeat monitor added');
        } else if (fd.get('kind') === 'http') {
          await api('/api/monitors', {method:'POST',body:JSON.stringify({name:fd.get('name'),url:fd.get('url'),authorized:fd.get('authorized')==='on',...ruleData(fd)})});
          toast('Monitor added. First check scheduled.');
        } else {
          const data = Object.fromEntries(fd);
          data.authorized = fd.get('authorized') === 'on';
          for (const field of ['days', 'port', 'status']) {
            if (field in data) data[field] = Number(data[field]);
          }
          await api('/api/monitors/network', {method:'POST',body:JSON.stringify(data)});
          toast('Monitor added. First check scheduled.');
        }
        dash = await api('/api/dashboard');
        renderView();
      } catch(err) {
        toast(err.message);
        button.disabled = false;
      }
    });
  };

  window.overview = function monitoringOverview() {
    const n = counts();
    const active = (dash?.incidents || []).filter(i => !i.resolved_at);
    const list = getMonitors().slice(0, 7);
    $('work-content').innerHTML = `${summaryStrip()}<div class="bn-layout"><div class="bn-main-column">
      <section class="bn-panel bn-monitor-panel"><div class="bn-toolbar"><div><h3>Your monitors</h3><p>${n.total ? 'Latest observed states and check history' : 'Add your first monitor to begin'}</p></div><button class="bn-btn bn-btn-primary" data-view="monitors">Manage monitors ↗</button></div>
      <div class="bn-list-heading"><span>MONITOR</span><span>STATUS</span><span>RECENT CHECKS</span><span>SUCCESS / LATENCY</span><span>ACTIONS</span></div>
      ${list.length ? list.map(monitorItem).join('') : '<div class="bn-empty-state"><strong>No monitors yet</strong><p>Add a website or scheduled job to start monitoring.</p><button class="bn-btn bn-btn-primary" data-view="monitors">+ Add your first monitor</button></div>'}</section>
      <section class="bn-panel bn-recent-panel"><div class="bn-panel-title"><h3>Recent incidents</h3><button class="bn-btn bn-btn-quiet" data-view="incidents">View history ↗</button></div>
      ${active.length ? active.slice(0,5).map(i=>`<div class="bn-incident-item"><span class="bn-state-dot bn-down"></span><div><strong>${tidy(i.monitor_name)}</strong><span>${tidy(i.summary)}</span></div><time>${tidy(fmt(i.opened_at))}</time></div>`).join('') : '<div class="bn-clear">✓ No unresolved incidents recorded</div>'}</section>
      </div><aside class="bn-aside">${sideSnapshot()}<section class="bn-panel bn-tip"><h3>Quick actions</h3><button class="bn-btn bn-btn-primary bn-btn-wide" data-view="monitors">+ Add monitor</button><button class="bn-btn bn-btn-quiet bn-btn-wide" data-view="status">Manage status page ↗</button></section></aside></div>`;
  };

  function openInvestigator(monitorId, incidentId, fallback, snapshot = dash) {
    const monitor = (snapshot?.monitors || []).find(m => Number(m.id) === Number(monitorId));
    if (!monitor) return false;
    const incident = (snapshot?.incidents || []).find(i => Number(i.id) === Number(incidentId)
      && Number(i.monitor_id) === Number(monitorId) && (!fallback || i.opened_at === fallback.opened_at));
    investigateMonitor(monitor, incident || fallback, snapshot.checks || []);
    return true;
  }

  window.investigateOutage = (incident, snapshot) => openInvestigator(incident.monitor_id, incident.id, incident, snapshot);

  const main = document.getElementById('work-content');
  main?.addEventListener('input', e => {
    if (e.target.id === 'bn-search') { filterState.query = e.target.value; paintMonitorResults(); }
  });
  main?.addEventListener('change', e => {
    if (e.target.id === 'bn-filter') { filterState.status = e.target.value; paintMonitorResults(); }
  });
  main?.addEventListener('click', async e => {
    const button = e.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-heartbeat-token')) {
      if (!confirm('Replace this heartbeat key? Update your job with the new command. The old key stops working immediately.')) return;
      button.disabled = true;
      try {
        const {token}=await api('/api/monitors/'+num(button.dataset.heartbeatToken)+'/heartbeat-token',{method:'POST'});
        showHeartbeatCommand(token);
      } catch (error) { toast(error.message); }
      finally { button.disabled = false; }
      return;
    }
    if (button.hasAttribute('data-maintenance')) {
      const monitor = getMonitors().find(m => num(m.id) === num(button.dataset.maintenance));
      if (monitor) manageMaintenance(monitor);
      return;
    }
    if (button.hasAttribute('data-check-rules')) {
      const monitor=getMonitors().find(m=>num(m.id)===num(button.dataset.checkRules));
      if (monitor) editRules(monitor);
      return;
    }
    if (button.hasAttribute('data-investigate')) {
      openInvestigator(button.dataset.investigate, button.dataset.incidentId);
      return;
    }
    if (button.hasAttribute('data-bn-new')) {
      document.getElementById('bn-add-panel')?.scrollIntoView({behavior:'smooth', block:'center'});
      document.getElementById('bn-monitor-name')?.focus({preventScroll:true});
    }
    if (button.hasAttribute('data-bn-refresh')) {
      button.disabled = true;
      try { dash = await api('/api/dashboard'); renderView(); toast('Monitoring data refreshed'); }
      catch (err) { toast(err.message); button.disabled = false; }
    }
  });
})();
