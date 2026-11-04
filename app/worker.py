"""Run website checks from a host outside the primary server's network."""
import asyncio
import json
import os
import re
import urllib.request
from contextlib import closing
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from .main import account_for_probe_network, email_ready, now, probe, run_probe, send_email
from .network_checks import EXPIRY_INTERVALS

RESERVED_REGIONS = {'primary', 'maintenance', 'heartbeat'}


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(url):
    parts = urlsplit(url)
    return parts.scheme == 'https' and parts.hostname and not (
        parts.username or parts.password or parts.fragment or parts.port not in (None, 443)
    )


def config():
    base = os.getenv('BEEPNADO_URL', '').rstrip('/')
    token = os.getenv('WORKER_TOKEN', '')
    region = os.getenv('WORKER_REGION', 'outside').strip()
    interval = int(os.getenv('WORKER_INTERVAL', '300'))
    urls = list(dict.fromkeys(url.strip() for url in os.getenv('WORKER_WATCH_URLS', '').split(',') if url.strip()))
    recipient = os.getenv('WORKER_ALERT_EMAIL', '').strip()
    if base or token:
        if not https_url(base) or urlsplit(base).query or len(token) < 32:
            raise ValueError('Dashboard sync requires HTTPS BEEPNADO_URL and a 32+ character WORKER_TOKEN')
    if not urls and not base:
        raise ValueError('Set WORKER_WATCH_URLS or configure dashboard sync')
    if len(urls) > 20 or any(not https_url(url) for url in urls):
        raise ValueError('Set up to 20 HTTPS watch URLs, without credentials or fragments')
    if region in RESERVED_REGIONS or not re.fullmatch(r'[a-z0-9_-]{2,32}', region):
        raise ValueError('Use a unique WORKER_REGION of 2–32 lowercase letters, digits, dashes or underscores')
    if interval <= 0:
        raise ValueError('Set WORKER_INTERVAL to a positive number of seconds')
    if recipient and not (urls and email_ready()):
        raise ValueError('Worker email requires watch URLs and an independent SMTP configuration')
    state_path = Path(os.getenv('WORKER_STATE_DB', '/data/outside.sqlite3'))
    return base, token, region, interval, urls, recipient, state_path


def dashboard_request(base, token, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(base + path, data=body, headers={
        'X-Worker-Token': token, 'Content-Type': 'application/json',
    })
    opener = urllib.request.build_opener(RejectRedirects())
    with opener.open(request, timeout=12) as response:
        return json.load(response)


def open_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def init_state(path):
    with closing(open_state(path)) as db, db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS watch_state (
            url TEXT PRIMARY KEY, state TEXT NOT NULL, failures INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL, recipient TEXT NOT NULL,
            subject TEXT NOT NULL, message TEXT NOT NULL,
            created_at INTEGER NOT NULL, next_attempt INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
        );
        ''')


def record_watch(path, url, result, recipient, region):
    with closing(open_state(path)) as db, db:
        row = db.execute('SELECT state, failures FROM watch_state WHERE url=?', (url,)).fetchone()
        previous = row['state'] if row else 'pending'
        if result['state'] == 'unknown':
            if row and previous != 'down':
                db.execute('UPDATE watch_state SET failures=0 WHERE url=?', (url,))
            print(f"{now()} {region} {urlsplit(url).hostname}: check inconclusive", flush=True)
            return
        failures = 0 if result['state'] == 'up' else min(2, (row['failures'] if row else 0) + 1)
        state = 'up' if not failures else 'down' if failures >= 2 else previous
        db.execute('''INSERT INTO watch_state(url,state,failures) VALUES(?,?,?)
                      ON CONFLICT(url) DO UPDATE SET state=excluded.state,failures=excluded.failures''',
                   (url, state, failures))
        address = urlsplit(url)
        label = address.hostname + address.path
        timestamp = now()
        print(f"{timestamp} {region} {label}: {result['state']} (confirmed state: {state})", flush=True)
        if recipient and ((state == 'down' and previous != 'down') or (state == 'up' and previous == 'down')):
            subject = f'BeepNado public {"outage" if state == "down" else "recovery"}: {label}'
            evidence = result['error'] or f"HTTP {result['http_code']}"
            message = f'{label} was {state} from {region} at {timestamp}.\nEvidence: {evidence}'
            clock = int(time.time())
            db.execute('INSERT INTO outbox(url,recipient,subject,message,created_at,next_attempt) VALUES(?,?,?,?,?,?)',
                       (url, recipient, subject, message, clock, clock))


def deliver_alerts(path):
    clock = int(time.time())
    with closing(open_state(path)) as db, db:
        db.execute('DELETE FROM outbox WHERE created_at<?', (clock - 7 * 86400,))
        pending = db.execute('''SELECT * FROM outbox WHERE next_attempt<=?
            AND id IN (SELECT MIN(id) FROM outbox GROUP BY url) ORDER BY id LIMIT 20''', (clock,)).fetchall()
    for alert in pending:
        try:
            send_email(alert['recipient'], alert['subject'], alert['message'])
        except (OSError, ValueError, RuntimeError) as exc:
            delay = min(3600, 60 * 2 ** min(alert['attempts'], 6))
            with closing(open_state(path)) as db, db:
                db.execute('UPDATE outbox SET next_attempt=?,attempts=attempts+1 WHERE id=?',
                           (clock + delay, alert['id']))
            print(f'Worker alert deferred: {type(exc).__name__}', flush=True)
        else:
            with closing(open_state(path)) as db, db:
                db.execute('DELETE FROM outbox WHERE id=?', (alert['id'],))


async def watch_once(path, urls, recipient, region):
    semaphore = asyncio.Semaphore(8)

    async def check(url):
        async with semaphore:
            result = await account_for_probe_network(await probe(url, public_only=True))
            await asyncio.to_thread(record_watch, path, url, result, recipient, region)

    await asyncio.gather(*(check(url) for url in urls))


async def send_pending(path):
    await asyncio.to_thread(deliver_alerts, path)


async def sync_once(base, token, region, expiry_checks=None):
    targets = await asyncio.to_thread(dashboard_request, base, token, '/internal/targets?typed=true')
    semaphore = asyncio.Semaphore(8)
    active = targets.get('targets', [])
    if expiry_checks is None:
        expiry_checks = {}
    active_ids = {target['id'] for target in active}
    for monitor_id in set(expiry_checks) - active_ids:
        del expiry_checks[monitor_id]

    async def report(target):
        kind = target.get('kind', 'http')
        monitor_id = target['id']
        if kind in EXPIRY_INTERVALS and time.monotonic() < expiry_checks.get(monitor_id, 0):
            return
        async with semaphore:
            result = await run_probe(target['url'], kind, target.get('config', {}), public_only=True)
            result = await account_for_probe_network(result)
            try:
                await asyncio.to_thread(dashboard_request, base, token, '/internal/result',
                                        {'monitor_id': monitor_id, 'region': region, **result})
            except (OSError, ValueError) as exc:
                print(f'Worker result could not sync: {type(exc).__name__}', flush=True)
            else:
                if kind in EXPIRY_INTERVALS:
                    expiry_checks[monitor_id] = time.monotonic() + EXPIRY_INTERVALS[kind]

    await asyncio.gather(*(report(target) for target in active))


async def periodic(interval, task, *args):
    while True:
        started = time.monotonic()
        try:
            await task(*args)
        except Exception as exc:
            # A sync or mail failure must never stop the independent checks.
            print(f'{task.__name__} failed: {type(exc).__name__}', flush=True)
        await asyncio.sleep(max(1, interval - (time.monotonic() - started)))


async def main():
    try:
        base, token, region, interval, urls, recipient, path = config()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    loops = []
    if urls:
        init_state(path)
        loops.append(periodic(interval, watch_once, path, urls, recipient, region))
        if recipient:
            loops.append(periodic(15, send_pending, path))
    if base:
        loops.append(periodic(interval, sync_once, base, token, region, {}))
    await asyncio.gather(*loops)


if __name__ == '__main__':
    asyncio.run(main())
