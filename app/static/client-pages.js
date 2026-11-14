/* Client pages and reports use only the monitors selected for each client. */
(() => {
  const path = '/api/client-pages';
  const slugPattern = '[a-z0-9]+(-[a-z0-9]+)*';

  function reportText(report) {
    const lines = [
      report.title + ' — Monitoring report',
      report.since + ' to ' + report.until + ' (UTC)',
      ''
    ];
    if (!report.monitors.length) lines.push('No monitors selected.');
    for (const monitor of report.monitors) {
      const label = monitor.name.replace(/\s+/g, ' ').trim();
      const uncertain = monitor.samples - monitor.passed - monitor.failed;
      lines.push(label);
      const eligible = monitor.eligible_samples ?? monitor.samples;
      lines.push(eligible
        ? `${(100 * monitor.passed / eligible).toFixed(2)}% of eligible checks passed (${monitor.passed}/${eligible})`
        : 'No conclusive samples available in this period.');
      if (monitor.inconclusive_samples) lines.push(`${monitor.inconclusive_samples} inconclusive checks excluded from the pass rate.`);
      if (monitor.samples) lines.push(`${monitor.failed} confirmed-down checks · ${uncertain} unconfirmed or inconclusive`);
      if (monitor.avg_ms != null) lines.push(`Average passing response time ${monitor.avg_ms} ms`);
      lines.push(`${monitor.incidents} confirmed incident${monitor.incidents === 1 ? '' : 's'} overlapping this period`, '');
    }
    lines.push('Based on primary checks and heartbeat observations. Maintenance, inconclusive samples and secondary checks are excluded from pass rates.');
    lines.push('These are sampled observations. Check pass rates do not measure continuous uptime.');
    if (report.status_path) lines.push('', 'Live status ' + new URL(report.status_path, location.origin).href);
    return lines.join('\n');
  }

  function downloadReport(text, slug) {
    const url = URL.createObjectURL(new Blob([text], {type: 'text/plain;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = slug + '-monitoring-report.txt';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function showEditor(root, pages, selectedId) {
    const selected = pages.find(page => page.id === selectedId);
    const selectedMonitors = new Set(selected?.monitor_ids);
    const monitors = dash?.monitors || [];
    root.innerHTML = `<div class="client-page-layout">
      <section class="panel"><h3>Client pages</h3>
        <p class="billing-note">Choose which monitors each client can see. Save a private draft or publish a read-only status link.</p>
        <div class="client-page-list">${pages.map(page => `<button type="button" class="work-btn client-page-choice" data-client-id="${page.id}" aria-pressed="${page.id === selectedId}">
          <strong>${esc(page.title)}</strong><small>${page.published ? 'Published' : 'Draft'} · ${page.monitor_ids.length} selected</small>
        </button>`).join('')}</div>
        <button type="button" class="work-btn" id="new-client-page">+ New client page</button>
        <button type="button" class="work-btn alt" data-view="status">Account-wide status page ↗</button>
      </section>
      <section class="panel"><h3>${selected ? 'Edit client page' : 'New client page'}</h3>
        <form class="work-form" id="client-page-form">
          <label>Client page title<input name="title" value="${esc(selected?.title)}" minlength="3" maxlength="70" placeholder="Acme Service Status" required></label>
          <label>Link slug<input name="slug" value="${esc(selected?.slug)}" minlength="3" maxlength="40" pattern="${slugPattern}" placeholder="acme-status" required></label>
          <fieldset class="client-monitors"><legend>Monitors on this page</legend>
            ${monitors.length ? monitors.map(monitor => `<label><input type="checkbox" name="monitor" value="${monitor.id}" ${selectedMonitors.has(monitor.id) ? 'checked' : ''}> <span>${esc(monitor.name)}</span></label>`).join('') : '<p>Add a monitor in the Monitors tab first.</p>'}
          </fieldset>
          <label class="client-publish"><input type="checkbox" name="published" ${selected?.published ? 'checked' : ''}> Publish this status page</label>
          <p class="work-form-hint">Anyone with the published link can see the selected monitor names, status, maintenance dates, and recent incident dates.</p>
          <div class="client-actions"><button type="submit" class="btn btn-accent" ${monitors.length ? '' : 'disabled'}>Save client page</button>
            ${selected ? '<button type="button" class="work-btn alt" id="delete-client-page">Delete page</button>' : ''}
            ${selected?.published ? `<a href="/s/${esc(selected.slug)}" target="_blank" rel="noopener" class="work-btn alt">Open status page ↗</a>` : ''}</div>
          <p id="client-page-error" class="error-line" role="alert"></p>
        </form>
      </section>
      ${selected ? `<section class="panel client-report"><h3>Monitoring report</h3>
        <p class="billing-note">Generate a report from this page’s saved monitor selection, including private drafts.</p>
        <form id="client-report-form" class="client-actions"><label>Period <select name="days"><option value="30">Last 30 days</option><option value="7">Last 7 days</option></select></label>
          <button type="submit" class="work-btn">Generate report</button></form>
        <div id="client-report-result" aria-live="polite"></div>
      </section>` : ''}
    </div>`;

    root.querySelectorAll('[data-client-id]').forEach(button => {
      button.addEventListener('click', () => showEditor(root, pages, Number(button.dataset.clientId)));
    });
    root.querySelector('#new-client-page').addEventListener('click', () => {
      showEditor(root, pages);
      root.querySelector('[name="title"]').focus();
    });
    const form = root.querySelector('#client-page-form');
    const error = root.querySelector('#client-page-error');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const fields = new FormData(form);
      const monitorIds = fields.getAll('monitor').map(Number);
      if (!monitorIds.length) {
        error.textContent = 'Select at least one monitor.';
        return;
      }
      const button = form.querySelector('[type="submit"]');
      button.disabled = true;
      error.textContent = '';
      try {
        const result = await api(selected ? path + '/' + selected.id : path, {
          method: selected ? 'PUT' : 'POST',
          body: JSON.stringify({
            title: fields.get('title'), slug: fields.get('slug'), monitor_ids: monitorIds,
            published: fields.has('published')
          })
        });
        if (form.isConnected) renderClientPages(result.id);
      } catch (err) {
        error.textContent = err.message;
      } finally {
        button.disabled = false;
      }
    });

    const remove = root.querySelector('#delete-client-page');
    remove?.addEventListener('click', async () => {
      if (!confirm('Delete this client page? Its public link will stop working.')) return;
      remove.disabled = true;
      error.textContent = '';
      try {
        await api(path + '/' + selected.id, {method: 'DELETE'});
        if (form.isConnected) renderClientPages();
      } catch (err) {
        error.textContent = err.message;
      } finally {
        remove.disabled = false;
      }
    });

    const reportForm = root.querySelector('#client-report-form');
    reportForm?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = reportForm.querySelector('button');
      const result = root.querySelector('#client-report-result');
      const days = new FormData(reportForm).get('days');
      button.disabled = true;
      result.textContent = 'Preparing report…';
      try {
        const report = await api(path + '/' + selected.id + '/report?days=' + days);
        const text = reportText(report);
        result.innerHTML = `<label class="client-report-preview">Report preview<textarea readonly rows="12"></textarea></label>
          <div class="client-actions"><button type="button" class="work-btn" id="copy-client-report">Copy report</button>
          <button type="button" class="work-btn alt" id="download-client-report">Download text</button></div>`;
        const preview = result.querySelector('textarea');
        preview.value = text;
        result.querySelector('#copy-client-report').addEventListener('click', async () => {
          try {
            await navigator.clipboard.writeText(text);
            toast('Monitoring report copied');
          } catch {
            preview.focus();
            preview.select();
            toast('Report selected. Use your keyboard to copy.');
          }
        });
        result.querySelector('#download-client-report').addEventListener('click', () => downloadReport(text, selected.slug));
      } catch (err) {
        result.textContent = err.message;
      } finally {
        button.disabled = false;
      }
    });
  }

  window.renderClientPages = async function renderClientPages(selectedId) {
    const root = document.createElement('div');
    root.textContent = 'Loading client pages…';
    $('work-content').replaceChildren(root);
    try {
      const pages = await api(path);
      if (root.isConnected) showEditor(root, pages, selectedId ?? pages[0]?.id);
    } catch (err) {
      root.textContent = err.message;
    }
  };
})();
