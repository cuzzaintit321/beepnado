/* Dashboard-only sound for newly observed confirmed outages. */
(() => {
  const panel = $('outage-alarm');
  if (!panel) return;

  const toggle = $('outage-alarm-toggle');
  const test = $('outage-alarm-test');
  const status = $('outage-alarm-state');
  const banner = $('outage-alarm-banner');
  const summary = $('outage-alarm-summary');
  const list = $('outage-alarm-incidents');
  const muted = 'Muted. Arm it to hear alerts while this dashboard tab is open.';
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
    toggle.textContent = 'Arm alarm';
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
        ? 'Armed in this tab. Keep the dashboard open to hear outages.'
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
    toggle.textContent = 'Mute alarm';
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
  panel.hidden = false;
})();
