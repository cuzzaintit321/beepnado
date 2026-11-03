/* BeepNado dark monitoring console. Everything shown comes from /api/dashboard. */
(() => {
  'use strict';
  const filterState = { query: '', status: 'all' };
  const getMonitors = () => dash?.monitors || [];
  const getChecks = () => dash?.checks || [];
  const num = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
  const tidy = (v) => esc(String(v ?? ''));

  function counts() {
    const monitors = getMonitors();
    return {
      total: monitors.length,
      up: monitors.filter(m => m.state === 'up').length,
      down: monitors.filter(m => m.state === 'down').length,
      degraded: monitors.filter(m => m.state === 'degraded').length,
      pending: monitors.filter(m => !m.last_checked).length
    };
  }

  function recentBars(m) {
    const checks = getChecks().filter(c => Number(c.monitor_id) === Number(m.id)).slice(0, 24).reverse();
    const gaps = Math.max(0, 24 - checks.length);
    const bars = '<span class="bn-tick bn-empty" aria-hidden="true"></span>'.repeat(gaps) +
      checks.map(c => `<span class="bn-tick bn-${['up','down','degraded'].includes(c.state) ? c.state : 'empty'}" title="${tidy(fmt(c.checked_at))}: ${tidy(prettyState(c.state))}" aria-hidden="true"></span>`).join('');
    return `<div class="bn-history"><div class="bn-history-bars" role="img" aria-label="${checks.length} recent recorded checks for ${tidy(m.name)}">${bars}</div><span class="bn-history-caption">${checks.length ? checks.length + ' recent checks' : 'No checks yet'}</span></div>`;
  }

  function monitorItem(m) {
    const state = m.last_checked ? (m.state || 'pending') : 'pending';
    const id = num(m.id);
    const pct = m.samples_30d ? `${num(m.success_rate_30d).toFixed(2)}%` : '—';
    return `<article class="bn-monitor-row" aria-label="${tidy(m.name)}: ${tidy(prettyState(state))}">
      <div class="bn-monitor-identity"><span class="bn-state-dot bn-${tidy(state)}" aria-hidden="true"></span>
        <div class="bn-monitor-title"><strong>${tidy(m.name)}</strong><span class="bn-host" title="${tidy(m.url)}">${tidy(m.url)}</span></div></div>
      <div class="bn-monitor-meta"><span>${pill(state)}</span><small>${m.last_checked ? 'Checked ' + tidy(fmt(m.last_checked)) : 'Awaiting first check'}</small></div>
      ${recentBars(m)}
      <div class="bn-monitor-metrics"><strong>${pct}</strong><small>30d checks passed</small><span>${m.response_ms == null ? '—' : num(m.response_ms) + ' ms'} latest</span></div>
      <div class="bn-monitor-actions"><button class="bn-btn bn-btn-small" data-check="${id}" aria-label="Check ${tidy(m.name)} now">Check now</button><button class="bn-icon-btn" data-remove="${id}" aria-label="Remove ${tidy(m.name)}" title="Remove monitor">×</button></div>
    </article>`;
  }

  function filteredMonitors() {
    return getMonitors().filter(m => {
      const phrase = (String(m.name || '') + ' ' + String(m.url || '')).toLowerCase();
      const wanted = filterState.status;
      const status = m.last_checked ? m.state : 'pending';
      return phrase.includes(filterState.query.toLowerCase()) && (wanted === 'all' || status === wanted);
    });
  }

  function emptyList() {
    return '<div class="bn-empty-state"><div class="bn-empty-icon">◉</div><strong>No matching monitors</strong><p>Try another search or status filter, or add a public website.</p></div>';
  }

  function sideSnapshot() {
    const n = counts();
    const all = getMonitors();
    const samples = all.reduce((sum, m) => sum + num(m.samples_30d), 0);
    const passed = all.reduce((sum, m) => sum + num(m.samples_30d) * num(m.success_rate_30d) / 100, 0);
    const percent = samples ? (passed / samples * 100).toFixed(2) + '%' : '—';
    return `<section class="bn-panel bn-snapshot" aria-label="Current monitor status"><h3>Current status <span class="bn-live-dot" title="Latest observations, not live network status"></span></h3>
      <div class="bn-status-trio"><div><strong class="bn-color-down">${n.down}</strong><small>Down</small></div><div><strong class="bn-color-up">${n.up}</strong><small>Up</small></div><div><strong class="bn-color-pending">${n.degraded + n.pending}</strong><small>Pending</small></div></div>
      <div class="bn-snapshot-footer">Monitoring <strong>${n.total}</strong> of <strong>${num(dash?.limit)}</strong> allowed sites</div></section>
      <section class="bn-panel bn-facts"><h3>Last 30 days</h3><div class="bn-fact"><span>Check success rate</span><strong>${percent}</strong></div><div class="bn-fact"><span>Recorded primary checks</span><strong>${samples.toLocaleString()}</strong></div><div class="bn-fact"><span>Recorded incidents</span><strong>${(dash?.incidents || []).length}${(dash?.incidents || []).length === 30 ? '+' : ''}</strong></div><p>Check-based observations, not a time-weighted SLA. No data means no claim of uptime.</p></section>`;
  }

  function addForm() {
    const full = getMonitors().length >= num(dash?.limit);
    return `<section class="bn-panel bn-add-panel" id="bn-add-panel"><div class="bn-panel-title"><h3>Add a website</h3><span>+ New</span></div>
      <p>Monitor a public website you own or manage.</p><form class="work-form" id="monitor-form">
        <label>Monitor name<input id="bn-monitor-name" name="name" placeholder="My business website" minlength="2" maxlength="100" required ${full ? 'disabled' : ''}></label>
        <label>Website URL<input name="url" type="url" placeholder="https://example.com" required maxlength="1500" ${full ? 'disabled' : ''}></label>
        <label class="bn-consent"><input type="checkbox" name="authorized" required ${full ? 'disabled' : ''}> I own or am authorized to monitor this site.</label>
        <button class="bn-btn bn-btn-primary bn-btn-wide" type="submit" ${full ? 'disabled' : ''}>+ Add monitor</button>
      </form><p class="bn-note">${full ? 'Plan limit reached. Remove a monitor before adding another.' : 'First check runs automatically. Public HTTP(S) endpoints only; redirects are not followed.'}</p></section>`;
  }

  function summaryStrip() {
    const n = counts();
    return `<div class="bn-summary-strip" aria-label="Monitor totals">
      <div><span>Monitored websites</span><strong>${n.total}<small> / ${num(dash?.limit)}</small></strong></div>
      <div><span>Operational</span><strong class="bn-color-up">${n.up}</strong></div>
      <div><span>Confirmed down</span><strong class="bn-color-down">${n.down}</strong></div>
      <div><span>Pending / degraded</span><strong class="bn-color-pending">${n.pending + n.degraded}</strong></div>
    </div>`;
  }

  function monitorToolbar() {
    return `<div class="bn-toolbar"><div><h3>Monitors<span class="bn-count">${getMonitors().length}</span></h3><p>Live observations from your website checks</p></div>
      <div class="bn-toolbar-actions"><button class="bn-btn bn-btn-quiet" data-bn-refresh>↻ Refresh</button><button class="bn-btn bn-btn-primary" data-bn-new>+ New monitor</button></div></div>
      <div class="bn-filter-bar"><label class="bn-search-label" for="bn-search">Search monitors<input id="bn-search" type="search" placeholder="Search by name or URL" autocomplete="off" value="${tidy(filterState.query)}"></label>
      <label class="bn-filter-label" for="bn-filter">Status<select id="bn-filter"><option value="all" ${filterState.status==='all'?'selected':''}>All statuses</option><option value="up" ${filterState.status==='up'?'selected':''}>Up</option><option value="down" ${filterState.status==='down'?'selected':''}>Down</option><option value="degraded" ${filterState.status==='degraded'?'selected':''}>Degraded</option><option value="pending" ${filterState.status==='pending'?'selected':''}>Pending</option></select></label></div>`;
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
        <div class="bn-list-heading"><span>WEBSITE</span><span>STATUS</span><span>RECENT CHECKS</span><span>SUCCESS / LATENCY</span><span>ACTIONS</span></div>
        <div id="bn-monitor-results">${list.length ? list.map(monitorItem).join('') : emptyList()}</div>
        <div class="bn-list-footer" id="bn-results-count">${list.length} of ${getMonitors().length} monitors</div></section>
        <p class="bn-disclosure">Monitoring results reflect checks from this server's configured region. Same-host checks do not detect home Internet or server-wide outages.</p></div>
        <aside class="bn-aside">${sideSnapshot()}${addForm()}</aside></div>`;
    const form = document.getElementById('monitor-form');
    form?.addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(form);
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      try {
        await api('/api/monitors', {method: 'POST', body: JSON.stringify({name:fd.get('name'),url:fd.get('url'),authorized:fd.get('authorized')==='on'})});
        toast('Monitor added. First check scheduled.');
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
      <section class="bn-panel bn-monitor-panel"><div class="bn-toolbar"><div><h3>Your websites</h3><p>${n.total ? 'Latest observed states and check history' : 'Add your first website to begin monitoring'}</p></div><button class="bn-btn bn-btn-primary" data-view="monitors">Manage monitors ↗</button></div>
      <div class="bn-list-heading"><span>WEBSITE</span><span>STATUS</span><span>RECENT CHECKS</span><span>SUCCESS / LATENCY</span><span>ACTIONS</span></div>
      ${list.length ? list.map(monitorItem).join('') : '<div class="bn-empty-state"><strong>No monitors yet</strong><p>Add a website to start collecting real check results.</p><button class="bn-btn bn-btn-primary" data-view="monitors">+ Add your first monitor</button></div>'}</section>
      <section class="bn-panel bn-recent-panel"><div class="bn-panel-title"><h3>Recent incidents</h3><button class="bn-btn bn-btn-quiet" data-view="incidents">View history ↗</button></div>
      ${active.length ? active.slice(0,5).map(i=>`<div class="bn-incident-item"><span class="bn-state-dot bn-down"></span><div><strong>${tidy(i.monitor_name)}</strong><span>${tidy(i.summary)}</span></div><time>${tidy(fmt(i.opened_at))}</time></div>`).join('') : '<div class="bn-clear">✓ No unresolved incidents recorded</div>'}</section>
      </div><aside class="bn-aside">${sideSnapshot()}<section class="bn-panel bn-tip"><h3>Quick actions</h3><button class="bn-btn bn-btn-primary bn-btn-wide" data-view="monitors">+ Add monitor</button><button class="bn-btn bn-btn-quiet bn-btn-wide" data-view="status">Manage status page ↗</button></section></aside></div>`;
  };

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