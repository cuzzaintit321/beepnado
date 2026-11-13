/* Persistent server-side email protection; optional dashboard-only sound. */
(() => {
  const panel = $('outage-alarm');
  if (!panel) return;

  const toggle = $('outage-alarm-toggle');
  const test = $('outage-alarm-test');
  const status = $('outage-alarm-state');
  const banner = $('outage-alarm-banner');
  const summary = $('outage-alarm-summary');
  const list = $('outage-alarm-incidents');
  const muted = 'Browser sound muted. Email protection is independent of this tab.';
  let audio;
  let silence;
  let session;
  let soundRequest;

  function setStatus(message) {
    if (status.textContent !== message) status.textContent = message;
  }

  function stop(message = muted) {
    const previous = session;
    session = undefined;
    clearTimeout(previous?.timer);
    previous?.request?.abort();
    previous?.release?.();
    soundRequest = undefined;
    silence?.();
    toggle.textContent = 'Enable sound';
    setStatus(message);
    showIncidents([]);
  }

  async function enableAudio() {
    audio ??= new AudioContext();
    await audio.resume();
    if (audio.state !== 'running') throw new Error('Sound is unavailable');
  }

  function play() {
    if (audio?.state !== 'running') return false;
    silence?.();
    const output = audio.createGain();
    output.connect(audio.destination);
    silence = () => {
      output.disconnect();
      silence = undefined;
    };
    [660, 880].forEach((frequency, index) => {
      const start = audio.currentTime + 0.02 + index * 0.22;
      const tone = audio.createOscillator();
      const envelope = audio.createGain();
      tone.frequency.value = frequency;
      envelope.gain.setValueAtTime(0, start);
      envelope.gain.linearRampToValueAtTime(0.12, start + 0.025);
      envelope.gain.linearRampToValueAtTime(0, start + 0.15);
      tone.connect(envelope).connect(output);
      tone.start(start);
      tone.stop(start + 0.16);
      const end = silence;
      tone.onended = () => {
        tone.disconnect();
        envelope.disconnect();
        if (index === 1 && silence === end) end();
      };
    });
    return true;
  }

  async function investigate(incident, button) {
    const armed = session;
    if (!armed) return;
    button.disabled = true;
    try {
      const data = await api('/api/dashboard');
      if (session !== armed || !signedIn || user?.id !== armed.account) return;
      if (!window.investigateOutage(incident, data)) toast('This monitor is no longer in your workspace.');
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function showIncidents(incidents) {
    const wasFocused = list.contains(document.activeElement);
    banner.hidden = incidents.length === 0;
    summary.textContent = incidents.length
      ? (incidents.length === 1 ? 'Confirmed outage' : incidents.length + ' confirmed outages')
      : '';
    list.replaceChildren(...incidents.map(incident => {
      const row = document.createElement('li');
      const name = document.createElement('strong');
      name.textContent = incident.monitor_name;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'bn-btn bn-btn-small';
      button.textContent = 'Investigate';
      button.setAttribute('aria-label', 'Investigate ' + incident.monitor_name);
      button.addEventListener('click', () => investigate(incident, button));
      row.append(name, button);
      return row;
    }));
    if (wasFocused) (list.querySelector('button') || toggle).focus();
  }

  async function poll(armed) {
    if (!signedIn || user?.id !== armed.account || $('workspace').classList.contains('hidden')) {
      stop();
      return;
    }
    const request = new AbortController();
    armed.request = request;
    const deadline = setTimeout(() => request.abort(), 10000);
    try {
      const response = await fetch('/api/alarm-incidents', {signal: request.signal});
      if (session !== armed) return;
      if (response.status === 401 || response.status === 403) {
        stop('Sign in again to arm the alarm.');
        return;
      }
      if (!response.ok) throw new Error('Alarm feed unavailable');
      const data = await response.json();
      if (session !== armed) return;
      if (data.account_id !== armed.account) {
        stop();
        return;
      }

      const active = new Map(data.incidents.map(incident => [
        [incident.id, incident.monitor_id, incident.opened_at].join('/'), incident
      ]));
      const fresh = armed.known ? [...active.keys()].filter(key => !armed.known.has(key)) : [];
      const previous = armed.alerts;
      armed.alerts = new Set([...previous, ...fresh].filter(key => active.has(key)));
      armed.known = active;
      if (fresh.length || previous.size !== armed.alerts.size) {
        showIncidents([...armed.alerts].map(key => active.get(key)));
      }
      if (fresh.length) play();
      setStatus(audio.state === 'running'
        ? 'Browser sound enabled while this tab is open.'
        : 'Browser paused audio. Click Test sound to resume it.');
    } catch {
      if (session === armed) setStatus('Alarm connection interrupted. Retrying…');
    } finally {
      clearTimeout(deadline);
      if (session === armed) armed.timer = setTimeout(() => poll(armed), 15000);
    }
  }

  toggle.addEventListener('click', async () => {
    if (session) {
      stop();
      return;
    }
    if (!signedIn || !user) return;

    const armed = {account: user.id, alerts: new Set()};
    session = armed;
    toggle.textContent = 'Mute sound';
    setStatus('Arming…');
    try {
      await enableAudio();
      if (session !== armed) return;
      if (!navigator.locks) {
        await poll(armed);
        return;
      }
      await navigator.locks.request('beepnado-outage-alarm', {ifAvailable: true}, lock => {
        if (session !== armed) return;
        if (!lock) {
          stop('The alarm is already armed in another tab.');
          return;
        }
        return new Promise(resolve => {
          armed.release = resolve;
          void poll(armed);
        });
      });
    } catch {
      if (session === armed) stop('Could not arm the alarm. Check browser audio and try again.');
    }
  });

  test.addEventListener('click', async () => {
    const request = Symbol();
    soundRequest = request;
    try {
      await enableAudio();
      if (soundRequest === request && signedIn && !$('workspace').classList.contains('hidden') && play()) {
        toast('Played the test alarm.');
      }
    } catch {
      if (soundRequest === request) toast('Browser audio is unavailable.');
    }
  });

  $('signout')?.addEventListener('click', () => stop());
  window.addEventListener('pagehide', () => stop());
  // Email alert state lives in the account database, not browser storage.
  const emailRow = document.createElement('div');
  emailRow.className = 'bn-alarm-toolbar';
  const emailStatus = document.createElement('p');
  emailStatus.setAttribute('role', 'status');
  const actions = document.createElement('div');
  actions.className = 'bn-alarm-actions';
  const emailToggle = document.createElement('button');
  const emailTest = document.createElement('button');
  for (const button of [emailToggle, emailTest]) {
    button.type = 'button';
    button.className = 'bn-btn bn-btn-small';
    button.disabled = true;
  }
  emailToggle.classList.add('bn-btn-primary');
  emailToggle.textContent = 'Arm email alerts';
  emailTest.textContent = 'Test email';
  actions.append(emailToggle, emailTest);
  emailRow.append(emailStatus, actions);
  panel.insertBefore(emailRow, panel.firstChild);
  let settings, account, revision = 0;
  function renderEmail() {
    emailToggle.disabled = !settings;
    emailTest.disabled = !settings?.armed || !settings?.email_ready;
    emailToggle.textContent = settings?.armed ? 'Disarm email alerts' : 'Arm email alerts';
    emailToggle.setAttribute('aria-pressed', String(!!settings?.armed));
    emailStatus.textContent = !settings ? 'Loading email protection…' : !settings.armed
      ? 'Email alerts disarmed. Monitoring continues.' : !settings.email_ready
      ? 'Email alerts armed, but server email delivery is not configured.'
      : 'Email alerts armed — outage and recovery emails go to ' + settings.email + ', even when you close this page.';
  }
  async function loadEmail() {
    if (!signedIn || !user || $('workspace').classList.contains('hidden')) {
      revision++; account = undefined; settings = undefined; renderEmail(); return;
    }
    if (account === user.id) return;
    account = user.id; settings = undefined; renderEmail();
    const version = ++revision;
    try {
      const result = await api('/api/alarm-settings');
      if (version === revision) { settings = result; renderEmail(); }
    } catch (error) {
      if (version === revision) { account = undefined; emailStatus.textContent = 'Unable to load email protection. Refresh to retry.'; }
    }
  }
  emailToggle.addEventListener('click', async () => {
    if (!settings || !signedIn || account !== user?.id) return;
    const version = revision;
    emailToggle.disabled = emailTest.disabled = true;
    try {
      const result = await api('/api/alarm-settings', {method: 'PUT', body: JSON.stringify({armed: !settings.armed})});
      if (version === revision) settings = result;
    } catch (error) { if (version === revision) toast(error.message); }
    finally { if (version === revision) renderEmail(); }
  });
  emailTest.addEventListener('click', async () => {
    if (!settings?.armed || !settings.email_ready || !signedIn || account !== user?.id) return;
    const version = revision;
    emailTest.disabled = true;
    try {
      await api('/api/alarm-settings/test', {method: 'POST'});
      if (version === revision) toast('Test email queued. Check your inbox shortly.');
    } catch (error) { if (version === revision) toast(error.message); }
    finally { if (version === revision) renderEmail(); }
  });
  new MutationObserver(loadEmail).observe($('workspace'), {attributes: true, attributeFilter: ['class']});
  void loadEmail();
  toggle.textContent = 'Enable sound';
  setStatus(muted);
  panel.hidden = false;
})();
