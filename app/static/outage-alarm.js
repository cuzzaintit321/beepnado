/* One account-level switch for persistent outage and recovery emails. */
(() => {
  const panel = $('outage-alarm');
  if (!panel) return;
  const toggle = $('outage-alarm-toggle');
  const status = $('outage-alarm-state');
  $('outage-alarm-test')?.remove();
  const banner = $('outage-alarm-banner');
  const summary = $('outage-alarm-summary');
  const list = $('outage-alarm-incidents');
  let settings, account, revision = 0, busy = false, timer;
  const active = () => signedIn && user && !$('workspace').classList.contains('hidden');
  function render() {
    toggle.disabled = busy || !settings;
    toggle.textContent = settings?.armed ? 'Disarm' : 'Arm';
    toggle.setAttribute('aria-pressed', String(!!settings?.armed));
    status.textContent = !settings ? 'Loading alert protection…' : !settings.armed
      ? 'Disarmed — monitoring continues without new incident emails.' : !settings.email_ready
      ? 'Armed — email delivery is not configured on the server.'
      : 'Armed — outage and recovery emails remain enabled even when you close this page.';
  }
  function showIncidents(incidents) {
    const focused = list.contains(document.activeElement);
    banner.hidden = !incidents.length;
    summary.textContent = incidents.length === 1 ? 'Confirmed outage' : incidents.length + ' confirmed outages';
    list.replaceChildren(...incidents.map(incident => {
      const row = document.createElement('li');
      const name = document.createElement('strong');
      name.textContent = incident.monitor_name;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'bn-btn bn-btn-small';
      button.textContent = 'Investigate';
      button.setAttribute('aria-label', 'Investigate ' + incident.monitor_name);
      button.addEventListener('click', async () => {
        const version = revision;
        button.disabled = true;
        try {
          const data = await api('/api/dashboard');
          if (version === revision && active() && account === user.id &&
              !window.investigateOutage(incident, data)) toast('This monitor is no longer in your workspace.');
        } catch (error) { if (version === revision) toast(error.message); }
        finally { button.disabled = false; }
      });
      row.append(name, button);
      return row;
    }));
    if (focused) (list.querySelector('button') || toggle).focus();
  }
  async function poll(version) {
    if (version !== revision || !active() || account !== user.id) return;
    try {
      const data = await api('/api/alarm-incidents');
      if (version === revision && active() && data.account_id === account) showIncidents(data.incidents);
    } catch { /* Keep email protection independent of the dashboard feed. */ }
    finally { if (version === revision) timer = setTimeout(() => poll(version), 15000); }
  }
  async function load() {
    if (!active()) {
      revision++; clearTimeout(timer); account = undefined; settings = undefined; busy = false;
      showIncidents([]); render(); return;
    }
    if (account === user.id) return;
    clearTimeout(timer);
    account = user.id; settings = undefined; busy = false; render();
    const version = ++revision;
    try {
      const result = await api('/api/alarm-settings');
      if (version === revision) { settings = result; render(); void poll(version); }
    } catch {
      if (version === revision) {
        account = undefined;
        status.textContent = 'Unable to load alert protection. Refresh to retry.';
      }
    }
  }
  toggle.addEventListener('click', async () => {
    if (busy || !settings || !active() || account !== user.id) return;
    const version = revision;
    busy = true; render();
    try {
      const result = await api('/api/alarm-settings', {
        method: 'PUT', body: JSON.stringify({armed: !settings.armed})
      });
      if (version === revision) settings = result;
    } catch (error) { if (version === revision) toast(error.message); }
    finally { if (version === revision) { busy = false; render(); } }
  });
  new MutationObserver(load).observe($('workspace'), {attributes: true, attributeFilter: ['class']});
  window.addEventListener('pagehide', () => { revision++; clearTimeout(timer); account = undefined; });
  window.addEventListener('pageshow', () => { void load(); });
  render();
  panel.hidden = false;
  void load();
})();
