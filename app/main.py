"""BeepNado: self-hosted multi-account uptime SaaS prototype.

Secure defaults for local, private testing. Public internet production deployment
requires independent security review, TLS reverse proxy, rate limits, backups and
separate worker regions. Payment entitlements change only after Stripe webhooks.
"""
from __future__ import annotations
import asyncio
import codecs
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
import secrets
import sqlite3
import ssl
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import h11
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import AwareDatetime, BaseModel, Field

from .network_checks import (
    EXPIRY_INTERVALS, NetworkInput, check_hex, normalize_dns, probe_network,
    public_hostname, resolve_public, result as network_result,
)

from . import integrations, infrastructure, advanced_checks, browser_checks, teams
from .journeys import JourneyInput, JourneySettings, JourneyTrace, journey_origin, probe_journey

ROOT = Path(__file__).resolve().parent
DB_PATH = os.getenv('DATABASE_PATH', '/data/beepnado.sqlite3')
SESSION_SECURE = os.getenv('SESSION_SECURE', 'true').lower() == 'true'
WORKER_TOKEN = os.getenv('WORKER_TOKEN', '')
CONFIRM_REGION = os.getenv('CONFIRM_REGION', '').strip().lower()
SITE_URL = os.getenv('SITE_URL', 'http://localhost:8490').rstrip('/')
REGISTRATION_ENABLED = os.getenv('REGISTRATION_ENABLED', 'false').lower() == 'true'
MAX_USERS = max(1, min(int(os.getenv('MAX_USERS', '50')), 10000))
SMTP_HOST = os.getenv('SMTP_HOST', '').strip()
SMTP_FROM = os.getenv('SMTP_FROM', '').strip()
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '').strip()
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_SSL = os.getenv('SMTP_SSL', 'false').lower() == 'true'
SMTP_STARTTLS = os.getenv('SMTP_STARTTLS', 'true').lower() == 'true'
TRUSTED_PROXY_IP = os.getenv('TRUSTED_PROXY_IP', '').strip()
STRIPE_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_SIGNING = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_TEST_EMAIL = os.getenv('STRIPE_TEST_EMAIL', '').strip().lower()
STRIPE_LIVE_MODE = STRIPE_KEY.startswith(('sk_live_', 'rk_live_'))
STRIPE_LIVE_CHECKOUT_ENABLED = os.getenv('STRIPE_LIVE_CHECKOUT_ENABLED', '').lower() == 'true'
STRIPE_PORTAL_CONFIGURATION = os.getenv('STRIPE_PORTAL_CONFIGURATION', '')
LIVE_WEBHOOK_LOCK = asyncio.Lock()
# Legacy price IDs remain recognized for existing subscriptions only.
PRICE_IDS = {'pro': os.getenv('STRIPE_PRICE_PRO',''), 'team': os.getenv('STRIPE_PRICE_TEAM','')}
PLAN_LIMITS = {'free': 50, 'pro': 50, 'team': 150, 'scale': 300}
PLAN_INTERVALS = {'free': 300, 'pro': 60, 'team': 30, 'scale': 15}
PLAN_MONTHLY = {'free': 0, 'pro': 9, 'team': 29, 'scale': 59}
NEW_PRICE_IDS = {(plan,cycle): os.getenv(f'STRIPE_PRICE_{plan.upper()}_{cycle.upper()}','')
                 for plan in ('pro','team','scale') for cycle in ('month','year')}

def price_mapping():
    result={price:plan for plan,price in PRICE_IDS.items() if price}
    for (plan,cycle),price in NEW_PRICE_IDS.items():
        if price:
            if price in result and result[price]!=plan:
                raise ValueError('Stripe price is assigned to multiple plans')
            result[price]=plan
    return result

def pricing_catalog():
    return [{'id':p,'monthly':PLAN_MONTHLY[p],'annual':PLAN_MONTHLY[p]*10,
             'monitors':PLAN_LIMITS[p],'interval_seconds':PLAN_INTERVALS[p]}
            for p in PLAN_LIMITS]
# Keep the oldest monitors active on downgrade. Later upgrades restore capacity
# automatically because this filter leaves monitor rows and history intact.
MONITOR_PLAN_LIMIT = 'CASE u.plan ' + ' '.join(
    f"WHEN '{plan}' THEN {limit}" for plan, limit in PLAN_LIMITS.items()
) + f" ELSE {PLAN_LIMITS['free']} END"
ACTIVE_MONITORS = f'''
    FROM monitors m JOIN users u ON u.id=m.user_id
    WHERE m.enabled=1 AND (
        SELECT COUNT(*) FROM monitors earlier
        WHERE earlier.user_id=m.user_id AND earlier.enabled=1 AND earlier.id<m.id
    ) < ({MONITOR_PLAN_LIMIT})
'''
COOKIE = 'beepnado_session'

# Narrow workaround for owner-operated domains on the same Docker host.
# DNS validation still happens against public IPs; only exact operator-configured
# names pointing at the operator's public IP may connect to the Caddy container.
# This route monitors local service health, NOT external Internet reachability.
SELF_HOSTED_PUBLIC_IP = os.getenv('SELF_HOSTED_PUBLIC_IP', '').strip()
SELF_HOSTED_DOMAINS = frozenset(
    h.strip().lower().rstrip('.') for h in os.getenv('SELF_HOSTED_DOMAINS', '').split(',') if h.strip()
)
SELF_HOSTED_CADDY = 'jason-portfolio'  # fixed service name on portfolio_default


def probe_connection_target(host: str, validated_public_ip: str) -> str:
    """Keep public DNS/SSRF checks; route only explicitly allowed own domains."""
    if (SELF_HOSTED_PUBLIC_IP
            and validated_public_ip == SELF_HOSTED_PUBLIC_IP
            and host in SELF_HOSTED_DOMAINS
            and ipaddress.ip_address(validated_public_ip).is_global):
        return SELF_HOSTED_CADDY
    return validated_public_ip


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free',
          stripe_customer TEXT UNIQUE, stripe_subscription TEXT UNIQUE, subscription_status TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          csrf TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS monitors(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL, url TEXT NOT NULL, created_at TEXT NOT NULL,
          last_checked TEXT, state TEXT NOT NULL DEFAULT 'pending', consecutive_failures INTEGER NOT NULL DEFAULT 0,
          http_code INTEGER, response_ms INTEGER, cert_days INTEGER, error TEXT,
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS monitors_due ON monitors(user_id,last_checked);
        CREATE TABLE IF NOT EXISTS checks(
          id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          region TEXT NOT NULL, checked_at TEXT NOT NULL, state TEXT NOT NULL,
          http_code INTEGER, response_ms INTEGER, cert_days INTEGER, error TEXT
        );
        CREATE INDEX IF NOT EXISTS checks_history ON checks(monitor_id,id DESC);
        CREATE INDEX IF NOT EXISTS checks_report_period ON checks(monitor_id,checked_at)
          WHERE region IN ('primary','heartbeat');
        CREATE INDEX IF NOT EXISTS checks_external_recent ON checks(monitor_id,checked_at)
          WHERE region NOT IN ('primary','maintenance','heartbeat');
        CREATE TABLE IF NOT EXISTS maintenance_windows(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, CHECK(starts_at < ends_at)
        );
        CREATE INDEX IF NOT EXISTS maintenance_monitor_end ON maintenance_windows(monitor_id,ends_at DESC);
        CREATE TABLE IF NOT EXISTS incidents(
          id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          opened_at TEXT NOT NULL, resolved_at TEXT, summary TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS incidents_open ON incidents(monitor_id) WHERE resolved_at IS NULL;
        CREATE TABLE IF NOT EXISTS status_pages(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
          slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS client_pages(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS client_pages_by_user ON client_pages(user_id,id);
        CREATE TABLE IF NOT EXISTS client_page_monitors(
          page_id INTEGER NOT NULL REFERENCES client_pages(id) ON DELETE CASCADE,
          monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          PRIMARY KEY(page_id,monitor_id)
        );
        CREATE INDEX IF NOT EXISTS client_page_monitors_by_monitor ON client_page_monitors(monitor_id);
        CREATE TABLE IF NOT EXISTS notifications(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notifications_by_user ON notifications(user_id,id DESC);
        CREATE TABLE IF NOT EXISTS billing_events(event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS billing_checkout(
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          livemode INTEGER NOT NULL, plan TEXT NOT NULL, request_key TEXT NOT NULL,
          created_at INTEGER NOT NULL, customer TEXT, session_id TEXT,
          PRIMARY KEY (user_id, livemode)
        );
        CREATE TABLE IF NOT EXISTS auth_tokens(
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),
          expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits(
          key TEXT PRIMARY KEY, window_start INTEGER NOT NULL, count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS email_outbox(
          id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
          created_at TEXT NOT NULL, next_attempt INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          sent_at TEXT
        );
        ''')
        integrations.init(c)
        infrastructure.init(c)
        browser_checks.init(c)
        teams.init(c)
        # Additive migration: preserve all pre-existing accounts and monitor history.
        columns={r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'email_verified_at' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN email_verified_at TEXT')
        if 'email_alerts_enabled' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN email_alerts_enabled INTEGER NOT NULL DEFAULT 1')
        monitor_columns = {r['name'] for r in c.execute('PRAGMA table_info(monitors)')}
        for name, column_type in (
            ('required_phrase', "TEXT NOT NULL DEFAULT ''"),
            ('expected_status', 'INTEGER'),
            ('max_response_ms', 'INTEGER'),
            ('maintenance_last_checked', 'TEXT'),
            ('probe_kind', "TEXT NOT NULL DEFAULT 'http'"),
            ('probe_config', "TEXT NOT NULL DEFAULT '{}'"),
            ('detail', 'TEXT'),
            ('heartbeat_token_hash', 'TEXT'),
            ('heartbeat_interval_s', 'INTEGER'),
            ('heartbeat_grace_s', 'INTEGER'),
            ('heartbeat_due_at', 'INTEGER'),
            ('heartbeat_last_at', 'INTEGER'),
        ):
            if name not in monitor_columns:
                c.execute(f'ALTER TABLE monitors ADD COLUMN {name} {column_type}')
        check_columns = {r['name'] for r in c.execute('PRAGMA table_info(checks)')}
        for column in ('detail', 'journey_trace', 'timings'):
            if column not in check_columns:
                c.execute(f'ALTER TABLE checks ADD COLUMN {column} TEXT')
        if 'inconclusive' not in check_columns:
            c.execute('ALTER TABLE checks ADD COLUMN inconclusive INTEGER NOT NULL DEFAULT 0')
            c.execute("UPDATE checks SET inconclusive=1 WHERE state='degraded' AND (detail LIKE '%connectivity is unverified%' OR error LIKE '%monitoring network could not reach%')")
        incident_columns = {r['name'] for r in c.execute('PRAGMA table_info(incidents)')}
        for column in ('opening_evidence', 'recovery_evidence'):
            if column not in incident_columns:
                c.execute(f'ALTER TABLE incidents ADD COLUMN {column} TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS heartbeat_token ON monitors(heartbeat_token_hash)')
        c.execute('''CREATE INDEX IF NOT EXISTS heartbeat_due ON monitors(heartbeat_due_at)
                     WHERE heartbeat_interval_s IS NOT NULL AND state!='down' ''')


def make_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + derived.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split(':', 1)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def email_ready():
    return bool(SMTP_HOST and SMTP_FROM and (SMTP_SSL or SMTP_STARTTLS))


def send_email(recipient: str, subject: str, content: str):
    """SMTP with TLS; no secrets or verification tokens are logged."""
    if not email_ready():
        raise RuntimeError('Verified SMTP is required before sending mail')
    msg=EmailMessage()
    msg['From']=SMTP_FROM
    msg['To']=recipient
    msg['Subject']=subject
    msg['Date'] = formatdate(usegmt=True)
    msg.set_content(content)
    if SMTP_SSL:
        server=smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=12, context=ssl.create_default_context())
    else:
        server=smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12)
    try:
        if not SMTP_SSL:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        try: server.quit()
        except (OSError, smtplib.SMTPException): server.close()


def new_auth_token(c, user_id: int, purpose: str, hours: int):
    raw=secrets.token_urlsafe(36)
    c.execute('DELETE FROM auth_tokens WHERE user_id=? AND purpose=?',(user_id,purpose))
    expires=(datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(timespec='seconds')
    c.execute('INSERT INTO auth_tokens(token_hash,user_id,purpose,expires_at) VALUES(?,?,?,?)',
              (hashlib.sha256(raw.encode()).hexdigest(),user_id,purpose,expires))
    return raw


def throttle(request: Request, action: str, subject: str, limit: int, seconds: int):
    # Source IP is the *socket* peer, not untrusted X-Forwarded-For input.
    ip=request.client.host if request.client else 'unknown'
    if TRUSTED_PROXY_IP and ip == TRUSTED_PROXY_IP:
        # Only honor Caddy's proxy-set header when the TCP peer is our exact Caddy container.
        forwarded=request.headers.get('x-forwarded-for','').split(',')[0].strip()
        try:
            if forwarded and ipaddress.ip_address(forwarded): ip=forwarded
        except ValueError: pass
    key=hashlib.sha256(f'{action}:{ip}:{subject.lower()}'.encode()).hexdigest()
    clock=int(time.time())
    with db() as c:
        existing=c.execute('SELECT window_start,count FROM rate_limits WHERE key=?',(key,)).fetchone()
        if existing and clock-existing['window_start']<seconds:
            if existing['count']>=limit:raise HTTPException(429,'Too many attempts; try again later')
            c.execute('UPDATE rate_limits SET count=count+1 WHERE key=?',(key,))
        else:
            c.execute('INSERT INTO rate_limits(key,window_start,count) VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start,count=1',(key,clock))


def enqueue_alert(c, user_id: int, subject: str, text: str, monitor_id=None, incident_id=None, recovery=False):
    u=c.execute('SELECT email,email_verified_at,email_alerts_enabled FROM users WHERE id=?',(user_id,)).fetchone()
    if u and u['email_verified_at'] and u['email_alerts_enabled']:
        integrations.enqueue(c,user_id,subject,text,monitor_id,incident_id,recovery)
    if email_ready() and u and u['email_verified_at'] and u['email_alerts_enabled']:
        c.execute('INSERT INTO email_outbox(recipient,subject,body,created_at,next_attempt) VALUES(?,?,?,?,?)',
                  (u['email'],subject,text,now(),int(time.time())))


def send_pending_alerts():
    if not email_ready():return
    with db() as c:
        rows=[dict(x) for x in c.execute('SELECT id,recipient,subject,body,attempts FROM email_outbox WHERE sent_at IS NULL AND next_attempt<=? AND attempts<7 ORDER BY id LIMIT 8',(int(time.time()),))]
    for row in rows:
        try:
            send_email(row['recipient'],row['subject'],row['body'])
            with db() as c:c.execute('UPDATE email_outbox SET sent_at=? WHERE id=? AND sent_at IS NULL',(now(),row['id']))
        except Exception as exc:
            # SMTP failures are retried; do not print email addresses, secrets, or message bodies.
            print('Email delivery deferred:',type(exc).__name__,flush=True)
            delay=min(3600,60*(2**min(row['attempts'],6)))
            with db() as c:c.execute('UPDATE email_outbox SET attempts=attempts+1,next_attempt=? WHERE id=?',(int(time.time())+delay,row['id']))


def create_session(c, user_id):
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc)+timedelta(days=7)).isoformat(timespec='seconds')
    c.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)',
              (hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, expires))
    return token


def session_token(request: Request):
    authorization = request.headers.get('authorization', '')
    if authorization:
        match = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{54})', authorization, flags=re.ASCII | re.IGNORECASE)
        return match[1] if match else ''
    return request.cookies.get(COOKIE, '')


def session_user(request: Request):
    token = session_token(request)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as c:
        row = c.execute('''SELECT u.id,u.email,u.email_verified_at,u.plan,u.stripe_customer,u.subscription_status,s.csrf
                           FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token_hash=? AND s.expires_at>?''', (token_hash,now())).fetchone()
    return dict(row) if row else None


def require_actor(request: Request):
    user = session_user(request)
    if not user:
        raise HTTPException(401, 'Sign in to continue')
    if not user['email_verified_at']:
        raise HTTPException(403, 'Verify your email before accessing monitoring')
    return user


def require_user(request: Request):
    actor=require_actor(request)
    requested=request.headers.get('x-workspace-id')
    # Image tags cannot attach headers; only this private GET supports explicit scope in its query.
    if not requested and request.method=='GET' and re.fullmatch(r'/api/browser/[0-9]+/image/[0-9]+',request.url.path):
        requested=request.query_params.get('workspace')
    with db() as c:
        user=teams.scope(c,actor,requested)
    request.state.workspace_user=user
    return user


def actor_csrf(request:Request,actor=Depends(require_actor)):
    if not hmac.compare_digest(request.headers.get('x-csrf-token',''),actor['csrf']):
        raise HTTPException(403,'Invalid CSRF token')
    return actor


def require_csrf(request: Request, user=Depends(require_user)):
    token = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(token, user['csrf']):
        raise HTTPException(403, 'Invalid CSRF token')
    if not teams.can_write(user['workspace_role'],request.method,request.url.path):
        raise HTTPException(403,'Your workspace role cannot perform this action')
    return user


async def validate_url(raw):
    """Reject private targets, credentials, redirects, custom ports, DNS rebinding."""
    try:
        u = urlsplit(raw)
        if u.scheme not in ('http','https') or not u.hostname or u.username is not None or u.password is not None or u.fragment or any(ord(ch)<32 for ch in raw):
            raise ValueError('Enter a public HTTP or HTTPS URL without credentials or fragments')
        if u.port not in (None, 80 if u.scheme=='http' else 443):
            raise ValueError('Only standard 80/443 ports are supported')
        host = public_hostname(u.hostname)
        port = 443 if u.scheme == 'https' else 80
        ip = await resolve_public(host, port)
        path = u.path or '/'
        if u.query: path += '?' + u.query
        if len(path)>1500 or any(ord(x)>127 for x in path):
            raise ValueError('URL path must be short and ASCII encoded')
        return host, ip, port, path, u.scheme
    except (ValueError, TypeError, OSError, UnicodeError, TimeoutError) as exc:
        raise ValueError(str(exc)) from exc


CONTENT_LIMIT = 256 * 1024


async def response_event(reader, connection):
    while (event := connection.next_event()) is h11.NEED_DATA:
        connection.receive_data(await reader.read(8192))
    return event


async def check_required_phrase(reader, connection, response, phrase):
    headers = dict(response.headers)
    if headers.get(b'content-encoding', b'identity').lower() != b'identity':
        return 'Server returned compressed content despite requesting an uncompressed response'
    charset = re.search(rb'charset\s*=\s*["\']?([a-z0-9_-]+)',
                        headers.get(b'content-type', b''), re.I)
    encoding = charset[1].decode('ascii').lower() if charset else 'utf-8'
    if encoding not in ('utf-8', 'utf8', 'ascii', 'us-ascii', 'latin-1', 'iso-8859-1', 'windows-1252', 'cp1252'):
        return 'Response character encoding is unsupported for phrase checks'
    decoder = codecs.getincrementaldecoder(encoding)(errors='replace')
    needle = phrase.casefold()
    overlap = ''
    remaining = CONTENT_LIMIT
    try:
        async with asyncio.timeout(6):
            while remaining:
                event = await response_event(reader, connection)
                if isinstance(event, h11.EndOfMessage):
                    final_text = overlap + decoder.decode(b'', final=True).casefold()
                    return None if needle in final_text else 'Required phrase missing from response'
                if not isinstance(event, h11.Data):
                    return 'Response ended before the required phrase could be checked'
                data = event.data[:remaining]
                remaining -= len(data)
                text = overlap + decoder.decode(data).casefold()
                if needle in text:
                    return None
                overlap = text[-len(needle):]
    except TimeoutError:
        return 'Timed out reading response content'
    except h11.RemoteProtocolError:
        return 'Invalid or incomplete HTTP response content'
    return 'Required phrase not found in the first 256 KiB of response content'


@asynccontextmanager
async def http_response(url, public_only, timings=None):
    clock = time.monotonic
    started = clock()
    host, ip, port, path, scheme = await validate_url(url)
    target = ip if public_only else probe_connection_target(host, ip)
    context = ssl.create_default_context() if scheme == 'https' else None
    if timings is not None:
        timings['dns_ms'] = round((clock()-started)*1000)
        timings['route'] = 'local' if target != ip else 'public'
    connected = clock()
    reader, writer = await asyncio.wait_for(asyncio.open_connection(target, port), timeout=8)
    try:
        if timings is not None:
            timings['tcp_ms'] = round((clock()-connected)*1000)
        if context:
            tls_started = clock()
            await writer.start_tls(context, server_hostname=host, ssl_handshake_timeout=8)
            if timings is not None:
                timings['tls_ms'] = round((clock()-tls_started)*1000)
        connection = h11.Connection(h11.CLIENT, max_incomplete_event_size=32 * 1024)
        request = h11.Request(method='GET', target=path, headers=[
            ('Host', host), ('User-Agent', 'BeepNado/0.1'),
            ('Accept-Encoding', 'identity'), ('Connection', 'close'),
        ])
        request_started = clock()
        writer.write(connection.send(request) + connection.send(h11.EndOfMessage()))
        await asyncio.wait_for(writer.drain(), 5)
        if timings is not None:
            class MeasuredReader:
                async def read(self, size):
                    data = await reader.read(size)
                    if data and 'ttfb_ms' not in timings:
                        timings['ttfb_ms'] = round((clock()-request_started)*1000)
                    return data
            response_reader = MeasuredReader()
        else:
            response_reader = reader
        async with asyncio.timeout(8):
            response = await response_event(response_reader, connection)
            while isinstance(response, h11.InformationalResponse) and response.status_code != 101:
                response = await response_event(response_reader, connection)
        if not isinstance(response, h11.Response):
            raise ValueError('Invalid or unsupported HTTP response')
        if timings is not None:
            timings['headers_ms'] = round((clock()-started)*1000)
        yield reader, connection, response, writer
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, TimeoutError, RuntimeError):
            pass


def reject_json_constant(value):
    raise ValueError('Invalid JSON number')


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('JSON number is outside the supported range')
    return number


async def read_json_response(reader, connection, response):
    if dict(response.headers).get(b'content-encoding', b'identity').lower() != b'identity':
        raise ValueError('The response used unsupported compression')
    body = bytearray()
    async with asyncio.timeout(6):
        while True:
            event = await response_event(reader, connection)
            if isinstance(event, h11.EndOfMessage):
                return json.loads(body, parse_constant=reject_json_constant, parse_float=finite_float)
            if not isinstance(event, h11.Data) or len(body) + len(event.data) > CONTENT_LIMIT:
                raise ValueError('JSON response is incomplete or larger than 256 KiB')
            body.extend(event.data)


async def public_json(url):
    if urlsplit(url).scheme != 'https':
        raise ValueError('Public lookup requires HTTPS')
    async with asyncio.timeout(12):
        async with http_response(url, public_only=True) as (reader, connection, response, _):
            if response.status_code != 200:
                raise ValueError(f'Lookup returned HTTP {response.status_code}')
            payload = await read_json_response(reader, connection, response)
    if not isinstance(payload, dict):
        raise ValueError('Lookup did not return a JSON object')
    return payload


def json_pointer(payload, path):
    for token in path.split('/')[1:]:
        key = token.replace('~1', '/').replace('~0', '~')
        if isinstance(payload, dict):
            payload = payload[key]
        elif isinstance(payload, list) and re.fullmatch(r'0|[1-9][0-9]*', key):
            payload = payload[int(key)]
        else:
            raise KeyError(key)
    return payload


async def check_api_json(reader, connection, response, config):
    try:
        payload = await read_json_response(reader, connection, response)
    except (ValueError, TimeoutError, h11.RemoteProtocolError, RecursionError):
        return 'API did not return a complete JSON response within 256 KiB', None
    path = config['json_path']
    try:
        value = json_pointer(payload, path)
    except (KeyError, IndexError):
        return f'JSON pointer {path} was missing', None
    if config['match'] and (value != config['expected'] or isinstance(value, bool) != isinstance(config['expected'], bool)):
        return f'JSON value at {path} did not match', None
    return None, f'JSON pointer {path} passed' if path else 'Valid JSON response'


async def measure_body(reader, connection, timings):
    # Bounded optional measurement; never change availability because a large body
    # or slow download exceeds this diagnostic budget.
    started = time.monotonic()
    size = 0
    try:
        async with asyncio.timeout(3):
            while True:
                event = await response_event(reader, connection)
                if isinstance(event, h11.EndOfMessage):
                    timings['body_ms'] = round((time.monotonic()-started)*1000)
                    timings['body_bytes'] = size
                    return
                if not isinstance(event, h11.Data):
                    return
                size += len(event.data)
                if size > CONTENT_LIMIT:
                    return
    except (TimeoutError, OSError, h11.RemoteProtocolError):
        return


async def probe(url, *, required_phrase='', expected_status=None, max_response_ms=None, public_only=False,
                api_config=None):
    started = time.monotonic()
    timings = {}
    try:
        async with http_response(url, public_only, timings) as (reader, connection, response, writer):
            code = response.status_code
            response_ms = int((time.monotonic() - started) * 1000)
            cert_days = None
            obj = writer.get_extra_info('ssl_object')
            cert = obj.getpeercert() if obj else None
            if cert and cert.get('notAfter'):
                cert_days = int((ssl.cert_time_to_seconds(cert['notAfter']) - time.time()) // 86400)
            error = detail = None
            if expected_status is not None and code != expected_status:
                error = f'Expected HTTP {expected_status}, received HTTP {code}'
            elif expected_status is None and code >= 400:
                error = f'HTTP {code}'
            elif required_phrase and 300 <= code < 400:
                error = 'Content check reached a redirect. Monitor the destination URL directly'
            elif max_response_ms is not None and response_ms > max_response_ms:
                error = f'Response too slow: {response_ms} ms exceeds {max_response_ms} ms'
            elif required_phrase:
                error = await check_required_phrase(reader, connection, response, required_phrase)
            elif api_config is not None:
                error, detail = await check_api_json(reader, connection, response, api_config)
            result = dict(state='down' if error else 'up', http_code=code,
                          response_ms=response_ms, cert_days=cert_days, error=error)
            if detail:
                result['detail'] = detail
            if not required_phrase and api_config is None and not error:
                await measure_body(reader, connection, timings)
            timings['total_ms'] = round((time.monotonic()-started)*1000)
            result['timings'] = timings
            return result
    except (h11.RemoteProtocolError, h11.LocalProtocolError):
        return dict(network_result(error='Invalid HTTP request or response'), timings=timings)
    except (ValueError, OSError, TypeError, TimeoutError) as exc:
        return dict(network_result(error=f'{type(exc).__name__}: {str(exc)[:90]}'), timings=timings)


async def run_probe(target, kind='http', config=None, *, public_only=False, **http_rules):
    if kind == 'http':
        return await probe(target, public_only=public_only, **http_rules)
    if kind == 'journey':
        return await probe_journey(target, config, probe, account_for_probe_network, public_only=public_only)
    if kind == 'api':
        return await probe(target, public_only=public_only, expected_status=config['status'], api_config=config)
    if kind in advanced_checks.PROTOCOL_PORTS or kind in advanced_checks.DNS_KINDS:
        if kind=='blacklist' and os.getenv('ENABLE_DNSBL','false').lower()!='true':
            return network_result(error='DNSBL provider is not enabled',unknown=True)
        return await advanced_checks.check(kind,target,config,public_json)
    route_tls = (lambda host, ip: ip) if public_only else probe_connection_target
    return await probe_network(kind, target, config, get_json=public_json, route_tls=route_tls)


EGRESS_PROBES = ('https://www.google.com/generate_204', 'https://one.one.one.one/')
egress_status = None
egress_failure_generation = 0
egress_lock = asyncio.Lock()


async def public_egress_available(*, fresh_after=None):
    global egress_status, egress_failure_generation
    async with egress_lock:
        if egress_status and time.monotonic() < egress_status[0] and (
                fresh_after is None or not egress_status[1] or
                (len(egress_status) > 2 and egress_status[2] >= fresh_after)):
            return egress_status[1]

        async def check(url):
            try:
                async with asyncio.timeout(6):
                    return (await probe(url, public_only=True))['http_code'] is not None
            except (TimeoutError, OSError, ValueError):
                return False

        started = time.monotonic()
        healthy = any(await asyncio.gather(*(check(url) for url in EGRESS_PROBES)))
        if not healthy:
            egress_failure_generation += 1
        egress_status = (time.monotonic() + 20, healthy, started)
        return healthy


async def account_for_probe_network(observation):
    # Never trust a positive connectivity result from before a failed check.
    # Even an HTTP error may originate from a local proxy during a network outage.
    if observation['state'] == 'down' and not await public_egress_available(fresh_after=time.monotonic()):
        uncertain = network_result(
            error='The monitoring network could not reach either public reference host',
            detail='Check inconclusive while public connectivity is unverified', unknown=True,
        )
        if observation.get('journey_trace'):
            trace = observation['journey_trace']
            steps = [dict(step) for step in trace['steps']]
            if steps and steps[-1]['state'] == 'down':
                steps[-1].update(state='unknown', error=uncertain['error'], http_code=None)
            uncertain['journey_trace'] = {**trace, 'steps': steps}
        return uncertain
    return observation


probe_locks = {}


def record_check(monitor_id, result):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        monitor = c.execute('SELECT m.* ' + ACTIVE_MONITORS + ' AND m.id=? AND m.heartbeat_interval_s IS NULL',
                            (monitor_id,)).fetchone()
        if not monitor:
            raise HTTPException(404, 'Monitor not found')
        timestamp = now()
        if c.execute('''SELECT 1 FROM maintenance_windows
                        WHERE monitor_id=? AND starts_at<=? AND ends_at>?''',
                     (monitor_id, timestamp, timestamp)).fetchone():
            state = 'degraded' if result['state'] == 'unknown' else result['state']
            store_check(c, monitor_id, 'maintenance', timestamp, state, result)
            c.execute('''UPDATE monitors SET maintenance_last_checked=?,
                         consecutive_failures=CASE WHEN state='degraded' THEN 0 ELSE consecutive_failures END
                         WHERE id=?''', (timestamp, monitor_id))
            return {'state': state, 'checked_at': timestamp, 'http_code': result['http_code'],
                    'response_ms': result['response_ms'], 'maintenance': True}
        if CONFIRM_REGION and result['state']=='down':
            # Require a fresh, matching remote observation. HTTP rule monitors are
            # confirmed only by workers that explicitly attest the current revision.
            cutoff=(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat(timespec='seconds')
            remote=c.execute('SELECT state,checked_at,inconclusive,detail FROM checks WHERE monitor_id=? AND region=? AND checked_at>=? ORDER BY id DESC LIMIT 1',
                             (monitor_id,CONFIRM_REGION,cutoff)).fetchone()
            if not remote or remote['state']!='down' or remote['inconclusive'] or remote['detail']!=('Confirmed revision '+worker_revision(monitor)):
                result=network_result(error='Awaiting independent outage confirmation',
                                      detail='Primary check failed; matching fresh public evidence unavailable',unknown=True)
        return record_observation(c, monitor, result, 'primary', timestamp)


def store_check(c, monitor_id, region, timestamp, state, result):
    trace = result.get('journey_trace')
    c.execute('''INSERT INTO checks(monitor_id,region,checked_at,state,http_code,response_ms,cert_days,error,detail,journey_trace,inconclusive,timings)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
              (monitor_id, region, timestamp, state, result['http_code'], result['response_ms'],
               result['cert_days'], result['error'], result.get('detail'), json.dumps(trace) if trace else None,
                int(result['state'] == 'unknown'), json.dumps(result['timings']) if result.get('timings') else None))


def incident_evidence(observation, timestamp, state, region):
    evidence = {
        'checked_at': timestamp, 'state': state, 'region': region,
        **{key: observation.get(key) for key in ('http_code', 'response_ms', 'cert_days', 'error', 'detail')},
    }
    if observation.get('timings'):
        evidence['timings'] = observation['timings']
    if observation.get('journey_trace'):
        evidence['journey_trace'] = observation['journey_trace']
    return json.dumps(evidence)


def record_observation(c, m, result, region, timestamp=None):
    monitor_id = m['id']
    heartbeat = region == 'heartbeat'
    unknown = result['state'] == 'unknown'
    failures=m['consecutive_failures']+1 if result['state']=='down' else 0
    state='down' if failures>=(1 if heartbeat else 2) else 'degraded' if failures>0 else 'up'
    if unknown:
        failures = m['consecutive_failures'] if m['state'] == 'down' else 0
        # An inconclusive sample cannot open or close a confirmed outage.
        state = 'down' if m['state'] == 'down' else 'degraded'
    timestamp = timestamp or now()
    c.execute('''UPDATE monitors SET last_checked=?,state=?,consecutive_failures=?,http_code=?,response_ms=?,
                 cert_days=?,error=?,detail=?,maintenance_last_checked=NULL WHERE id=?''',
      (timestamp,state,failures,result['http_code'],result['response_ms'],result['cert_days'],result['error'],
       result.get('detail'),monitor_id))
    store_check(c, monitor_id, region, timestamp, 'degraded' if unknown else state, result)
    if unknown:
        return {'state': 'degraded', 'checked_at': timestamp, 'http_code': result['http_code'],
                'response_ms': result['response_ms'], 'detail': result.get('detail')}
    incident=c.execute('SELECT id FROM incidents WHERE monitor_id=? AND resolved_at IS NULL ORDER BY id DESC LIMIT 1',(monitor_id,)).fetchone()
    if state=='down' and not incident:
        reason = result['error'] or 'Check failed'
        summary = f'{m["name"]} missed a heartbeat' if heartbeat else f'{m["name"]} failed its monitoring checks'
        opening = f'{m["name"]} missed a heartbeat' if heartbeat else f'Two checks failed for {m["name"]}'
        incident_id=c.execute('INSERT INTO incidents(monitor_id,opened_at,summary,opening_evidence) VALUES(?,?,?,?)',
                  (monitor_id, timestamp, summary, incident_evidence(result, timestamp, state, region))).lastrowid
        c.execute('INSERT INTO notifications(user_id,monitor_id,kind,message,created_at) VALUES(?,?,?,?,?)',
                  (m['user_id'],monitor_id,'outage',f'{m["name"]}: {reason}',timestamp))
        enqueue_alert(c,m['user_id'],'BeepNado outage: '+m['name'],
                      f'{opening} at {timestamp} UTC.\nLatest result: {reason}\nLog in to {SITE_URL} to investigate.',monitor_id,incident_id)
    elif state=='up' and incident:
        recovery = 'heartbeat received' if heartbeat else 'monitor check passed again'
        recovery_event = 'sent a heartbeat' if heartbeat else 'passed a check'
        c.execute('UPDATE incidents SET resolved_at=?,recovery_evidence=? WHERE id=?',
                  (timestamp, incident_evidence(result, timestamp, state, region), incident['id']))
        c.execute('INSERT INTO notifications(user_id,monitor_id,kind,message,created_at) VALUES(?,?,?,?,?)',
                  (m['user_id'],monitor_id,'recovery',f'{m["name"]}: {recovery}',timestamp))
        enqueue_alert(c,m['user_id'],'BeepNado recovery: '+m['name'],
                      f'{m["name"]} {recovery_event} at {timestamp} UTC.\nSee {SITE_URL} for observations.',monitor_id,incident['id'],True)
    return {'state':state,'checked_at':timestamp,'http_code':result['http_code'],'response_ms':result['response_ms']}


async def check_monitor(monitor_id, *, manual=False):
    lock=probe_locks.setdefault(monitor_id,asyncio.Lock())
    async with lock:
        with db() as c:
            mon=c.execute('SELECT m.* ' + ACTIVE_MONITORS + ' AND m.id=? AND m.heartbeat_interval_s IS NULL',
                          (monitor_id,)).fetchone()
        if not mon:
            return None
        if manual:
            last_check = max(mon['last_checked'] or '', mon['maintenance_last_checked'] or '')
            cooldown = 300 if mon['probe_kind'] in EXPIRY_INTERVALS else 15
            if last_check and (datetime.now(timezone.utc)-datetime.fromisoformat(last_check)).total_seconds() < cooldown:
                raise HTTPException(429, f'Wait {cooldown} seconds between manual checks')
        result = await run_probe(
            mon['url'], mon['probe_kind'], json.loads(mon['probe_config']),
            required_phrase=mon['required_phrase'], expected_status=mon['expected_status'],
            max_response_ms=mon['max_response_ms'],
        )
        return record_check(monitor_id, await account_for_probe_network(result))


async def scheduler():
    await asyncio.sleep(3)
    semaphore = asyncio.Semaphore(8)
    pending = {}
    next_cleanup = 0.0

    async def run(monitor_id):
        try:
            async with semaphore:
                await check_monitor(monitor_id)
        except Exception as exc:
            print('Check error', monitor_id, type(exc).__name__, flush=True)
        finally:
            pending.pop(monitor_id, None)

    try:
        while True:
            try:
                clock = datetime.now(timezone.utc)
                due = []
                with db() as c:
                    for row in c.execute('SELECT m.id,m.last_checked,m.maintenance_last_checked,m.probe_kind,u.plan '
                                         + ACTIVE_MONITORS + ' AND m.heartbeat_interval_s IS NULL'):
                        if row['id'] in pending:
                            continue
                        wait = EXPIRY_INTERVALS.get(row['probe_kind'], PLAN_INTERVALS.get(row['plan'], 300))
                        last_check = max(row['last_checked'] or '', row['maintenance_last_checked'] or '')
                        if not last_check or (clock - datetime.fromisoformat(last_check)).total_seconds() >= wait:
                            due.append(row['id'])
                    if time.monotonic() >= next_cleanup:
                        cutoff = (clock - timedelta(days=30)).isoformat(timespec='seconds')
                        c.execute('DELETE FROM checks WHERE checked_at<?', (cutoff,))
                        c.execute('DELETE FROM sessions WHERE expires_at<?', (now(),))
                        c.execute('DELETE FROM auth_tokens WHERE expires_at<?', (now(),))
                        c.execute('DELETE FROM rate_limits WHERE window_start<?', (int(time.time()) - 86400,))
                        next_cleanup = time.monotonic() + 60
                # Keep scanning for due monitors while probes run.
                for monitor_id in due:
                    pending[monitor_id] = asyncio.create_task(run(monitor_id))
            except Exception as exc:
                print('Scheduler error', type(exc).__name__, flush=True)
            await asyncio.sleep(2)
    finally:
        tasks = tuple(pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def heartbeat_observation(c, monitor, state, error=None):
    record_observation(c, monitor, {
        'state': state, 'error': error, 'http_code': None, 'response_ms': None, 'cert_days': None,
    }, 'heartbeat')


def expire_heartbeats(*, connectivity_ok=True, reset_overdue=False):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        overdue = c.execute('SELECT m.* ' + ACTIVE_MONITORS + '''
            AND m.heartbeat_interval_s IS NOT NULL AND m.state!='down' AND m.heartbeat_due_at<?
        ''', (int(time.time()),)).fetchall()
        if not connectivity_ok or reset_overdue:
            # A monitoring interruption breaks the consecutive-failure sequence.
            c.execute("UPDATE monitors SET consecutive_failures=0 WHERE state!='down' AND consecutive_failures>0")
        if connectivity_ok and reset_overdue:
            # Extend all non-down deadlines, not only those that have already passed.
            # A job may have attempted delivery just before connectivity recovered.
            for monitor in c.execute('SELECT m.* ' + ACTIVE_MONITORS +
                                     " AND m.heartbeat_interval_s IS NOT NULL AND m.state!='down'").fetchall():
                deadline = int(time.time()) + monitor['heartbeat_interval_s'] + monitor['heartbeat_grace_s']
                c.execute('UPDATE monitors SET heartbeat_due_at=MAX(heartbeat_due_at,?) WHERE id=?',
                          (deadline, monitor['id']))
            return
        for monitor in overdue:
            if not connectivity_ok:
                if monitor['error'] != 'Monitoring connectivity unavailable; heartbeat status inconclusive':
                    heartbeat_observation(c, monitor, 'unknown', 'Monitoring connectivity unavailable; heartbeat status inconclusive')
            else:
                heartbeat_observation(c, monitor, 'down', 'No heartbeat arrived before the deadline')


async def heartbeat_scheduler():
    await asyncio.sleep(3)
    interrupted = True
    previous_generation = egress_failure_generation
    last_cycle = None
    while True:
        try:
            started = time.monotonic()
            healthy = await public_egress_available(fresh_after=started)
            generation = egress_failure_generation
            gap = last_cycle is not None and started - last_cycle > 30
            await asyncio.to_thread(expire_heartbeats, connectivity_ok=healthy,
                                    reset_overdue=healthy and (interrupted or gap or generation != previous_generation))
            previous_generation = generation
            last_cycle = started
            interrupted = not healthy
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            interrupted = True
            print('Heartbeat scheduler error:', type(exc).__name__, flush=True)
        await asyncio.sleep(10)


async def email_delivery_scheduler():
    """Keep slow or unavailable SMTP from delaying primary website checks."""
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(send_pending_alerts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('Mail scheduler error:', type(exc).__name__, flush=True)
        await asyncio.sleep(15)


async def channel_delivery_scheduler():
    await asyncio.sleep(5)
    while True:
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                integrations.enqueue_escalations(c,SITE_URL,now(),ACTIVE_MONITORS)
                c.execute('DELETE FROM infrastructure_samples WHERE received_at<?',(int(time.time())-infrastructure.RETENTION,))
                c.execute('DELETE FROM browser_samples WHERE checked_at<?',(int(time.time())-browser_checks.RETENTION,))
            await integrations.drain(db,validate_url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print('Integration scheduler error:',type(exc).__name__,flush=True)
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app):
    init_db()
    tasks = [asyncio.create_task(run()) for run in (scheduler, heartbeat_scheduler, email_delivery_scheduler, channel_delivery_scheduler)]
    try:
        yield
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app=FastAPI(title='BeepNado SaaS',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)


@app.middleware('http')
async def headers(request: Request, call_next):
    if request.url.path.startswith(('/api/','/stripe/','/internal/')):
        try:
            if int(request.headers.get('content-length','0')) > 65536:
                return JSONResponse({'detail':'Request body too large'},status_code=413)
        except ValueError:
            return JSONResponse({'detail':'Invalid Content-Length'},status_code=400)
    if request.method in ('POST','PUT','PATCH','DELETE'):
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            host=request.headers.get('host','')
            parsed=urlsplit(origin)
            if parsed.netloc!=host or parsed.scheme not in ('http','https'):
                return JSONResponse({'detail':'Cross-origin request denied'},status_code=403)
        elif request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Cross-site request denied'},status_code=403)
    response=await call_next(request)
    scoped=getattr(request.state,'workspace_user',None)
    if scoped and request.method in ('POST','PUT','PATCH','DELETE') and response.status_code<300 and not request.url.path.startswith('/api/team/'):
        try:
            with db() as c:
                teams.audit(c,scoped['workspace_id'],scoped['actor_id'],request.method+' '+request.url.path)
        except Exception:
            print('Workspace audit write failed',flush=True)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",'Cache-Control':'no-store'})
    return response


def billing_account_allowed(user):
    if not (STRIPE_SIGNING and (any(PRICE_IDS.values()) or any(NEW_PRICE_IDS.values()))):
        return False
    if STRIPE_LIVE_MODE:
        return bool(STRIPE_PORTAL_CONFIGURATION)
    return bool(STRIPE_KEY.startswith(('sk_test_', 'rk_test_')) and STRIPE_TEST_EMAIL
                and user and user['email_verified_at'] and user['email'] == STRIPE_TEST_EMAIL)


def billing_allowed(user):
    return billing_account_allowed(user) and (not STRIPE_LIVE_MODE or STRIPE_LIVE_CHECKOUT_ENABLED)


@app.get('/api/availability')
def public_availability(request: Request):
    # Test checkout is only advertised to the configured, signed-in test account.
    user=session_user(request)
    if user and user.get('email_verified_at'):
        user=require_user(request)
    owner=bool(user and user.get('workspace_role')=='owner')
    allowed=owner and billing_allowed(user)
    return {'free_registration': bool(REGISTRATION_ENABLED and email_ready()),
            'billing': allowed,
            'billing_portal': bool(owner and user.get('stripe_customer') and billing_account_allowed(user)),
            'checkout_options': [f'{p}:{c}' for (p,c),price in NEW_PRICE_IDS.items() if allowed and price],
            'plans': pricing_catalog(),
            'email': email_ready()}


@app.get('/health')
def health():return {'ok':True,'name':'BeepNado'}


@app.get('/health/ready')
def ready():
    """Ready only when the persistent database is accessible."""
    try:
        with db() as c: c.execute('SELECT 1').fetchone()
    except (sqlite3.Error, OSError):
        raise HTTPException(503,'Database unavailable')
    return {'ok':True, 'database':'ready'}


@app.get('/status', response_class=HTMLResponse)
def service_status():
    try:
        ready()
    except HTTPException:
        status_code = 503
        status_message = 'Database readiness check failed'
    else:
        status_code = 200
        status_message = 'Application and database responding'
    template = (ROOT / 'static' / 'service-status.html').read_text(encoding='utf-8')
    page = template.replace('__CHECK_TIME__', now()).replace('__STATUS_MESSAGE__', status_message)
    page = page.replace('__STATUS_CLASS__', 'alert' if status_code == 503 else '')
    return HTMLResponse(page, status_code=status_code)


@app.get('/')
def home():
    return FileResponse(ROOT/'static'/'index.html', headers={'X-Robots-Tag': 'noindex'})


@app.get('/static/{filename}')
def static(filename:str):
    if filename not in ('site.css','app.js','favicon.svg','experience.css','experience.js','monitor-theme.css','monitor-view.js','client-pages.js','outage-alarm.js','integrations.js','infrastructure.js','browser.js','teams.js'):raise HTTPException(404)
    return FileResponse(ROOT/'static'/filename)


class Credentials(BaseModel):
    email: str=Field(min_length=5,max_length=160)
    password: str=Field(min_length=12,max_length=200)


@app.post('/api/register',status_code=202)
def register(input:Credentials,request:Request):
    if not REGISTRATION_ENABLED or not email_ready():
        raise HTTPException(503,'New registrations are not open yet')
    email=input.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Enter a valid email')
    throttle(request,'register-global','*',30,3600)
    throttle(request,'register-email',email,3,3600)
    with db() as c:
        row=c.execute('SELECT id,email_verified_at FROM users WHERE email=?',(email,)).fetchone()
        if row:
            if row['email_verified_at']:
                # No address enumeration; never send duplicate signup confirmation.
                return {'ok':True,'message':'If eligible, check your email for a verification link'}
            uid=row['id']
        else:
            # A small public beta must not permit unbounded free-account creation.
            if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= MAX_USERS:
                raise HTTPException(503,'The BeepNado beta is currently full')
            cur=c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',
                          (email,make_password(input.password),now()))
            uid=cur.lastrowid
        token=new_auth_token(c,uid,'verify',24)
    try:
        send_email(email,'Verify your BeepNado account',
                   f'Confirm your BeepNado email address:\n{SITE_URL}/#verify={token}\n\nThis link expires in 24 hours.\n\nIf you did not sign up, ignore this message.')
    except Exception as exc:
        print('Verification email failed:',type(exc).__name__,flush=True)
        raise HTTPException(502,'Could not send verification email. Please try again later') from exc
    return {'ok':True,'message':'Check your email to verify your account'}


def authenticate_credentials(input: Credentials, request: Request):
    email=input.email.strip().lower()
    throttle(request,'login-global','*',100,600)
    throttle(request,'login-email',email,8,600)
    with db() as c:
        row=c.execute('SELECT id,password_hash,email_verified_at FROM users WHERE email=?',(email,)).fetchone()
        if not row or not verify_password(input.password,row['password_hash']):
            raise HTTPException(401,'Invalid credentials')
        if not row['email_verified_at']:
            raise HTTPException(403,'Verify your email before signing in. Check your inbox or resend verification.')
        token=create_session(c,row['id'])
    return token


@app.post('/api/mobile/login')
def mobile_login(input: Credentials, request: Request):
    if request.headers.get('origin'):
        raise HTTPException(403, 'Use the website sign-in in a browser')
    return {'token': authenticate_credentials(input, request)}


@app.post('/api/login')
def login(input:Credentials,request:Request,response:Response):
    token=authenticate_credentials(input, request)
    response.set_cookie(COOKIE,token,httponly=True,secure=SESSION_SECURE,samesite='lax',max_age=604800,path='/')
    return {'ok':True}


class EmailOnly(BaseModel):
    email:str=Field(min_length=5,max_length=160)

class TokenOnly(BaseModel):
    token:str=Field(min_length=30,max_length=180)

class ResetInput(TokenOnly):
    password:str=Field(min_length=12,max_length=200)


@app.post('/api/verify-email')
def verify_email(input:TokenOnly,response:Response,request:Request):
    throttle(request,'verify-global','*',30,3600)
    lookup=hashlib.sha256(input.token.encode()).hexdigest()
    with db() as c:
        row=c.execute('SELECT user_id FROM auth_tokens WHERE token_hash=? AND purpose=? AND expires_at>?',
                      (lookup,'verify',now())).fetchone()
        if not row:raise HTTPException(400,'Verification link is invalid or expired')
        c.execute('UPDATE users SET email_verified_at=? WHERE id=?',(now(),row['user_id']))
        c.execute('DELETE FROM auth_tokens WHERE user_id=? AND purpose=?',(row['user_id'],'verify'))
        token=create_session(c,row['user_id'])
    response.set_cookie(COOKIE,token,httponly=True,secure=SESSION_SECURE,samesite='lax',max_age=604800,path='/')
    return {'ok':True}


@app.post('/api/resend-verification')
def resend_verification(input:EmailOnly,request:Request):
    if not email_ready():raise HTTPException(503,'Email is temporarily unavailable')
    email=input.email.strip().lower()
    throttle(request,'resend-email',email,3,3600)
    with db() as c:
        row=c.execute('SELECT id,email_verified_at FROM users WHERE email=?',(email,)).fetchone()
        if row and not row['email_verified_at']:
            raw=new_auth_token(c,row['id'],'verify',24)
        else:raw=None
    if raw:
        try:send_email(email,'Verify your BeepNado account',f'Confirm your email address:\n{SITE_URL}/#verify={raw}\n\nExpires in 24 hours.')
        except Exception as exc:raise HTTPException(502,'Unable to send email') from exc
    return {'ok':True,'message':'If your account needs verification, an email has been sent'}


@app.post('/api/request-password-reset')
def request_password_reset(input:EmailOnly,request:Request):
    if not email_ready():raise HTTPException(503,'Email is temporarily unavailable')
    email=input.email.strip().lower()
    throttle(request,'reset-email',email,3,3600)
    with db() as c:
        row=c.execute('SELECT id,email_verified_at FROM users WHERE email=?',(email,)).fetchone()
        raw=new_auth_token(c,row['id'],'reset',1) if row and row['email_verified_at'] else None
    if raw:
        try:send_email(email,'Reset your BeepNado password',f'Reset your password:\n{SITE_URL}/#reset={raw}\n\nExpires in one hour. Ignore this message if you did not request it.')
        except Exception as exc:raise HTTPException(502,'Unable to send email') from exc
    return {'ok':True,'message':'If the address is registered, a reset link has been sent'}


@app.post('/api/reset-password')
def reset_password(input:ResetInput,request:Request):
    throttle(request,'reset-token','*',20,3600)
    lookup=hashlib.sha256(input.token.encode()).hexdigest()
    with db() as c:
        row=c.execute('SELECT user_id FROM auth_tokens WHERE token_hash=? AND purpose=? AND expires_at>?',
                      (lookup,'reset',now())).fetchone()
        if not row:raise HTTPException(400,'Reset link is invalid or expired')
        c.execute('UPDATE users SET password_hash=? WHERE id=?',(make_password(input.password),row['user_id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],))
        c.execute('DELETE FROM auth_tokens WHERE user_id=?',(row['user_id'],))
    return {'ok':True}


@app.post('/api/logout')
def logout(request:Request,response:Response,user=Depends(actor_csrf)):
    token=session_token(request)
    with db() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@app.get('/api/me')
def me(request:Request):
    user=session_user(request)
    if user and user.get('email_verified_at'):
        user=require_user(request)
    return {'signed_in':bool(user and user.get('email_verified_at')),'user':user if user and user.get('email_verified_at') else None,
            'plans':{p:{'monitors':PLAN_LIMITS[p],'interval':PLAN_INTERVALS[p]} for p in PLAN_LIMITS}}


def scheduled_maintenance(c, user_id, clock):
    return {row['monitor_id']: dict(row) for row in c.execute('''
        SELECT w.id,w.monitor_id,w.starts_at,w.ends_at
        FROM maintenance_windows w JOIN monitors m ON m.id=w.monitor_id
        WHERE m.user_id=? AND w.ends_at>?
    ''', (user_id, clock))}


def displayed_state(monitor, window, clock):
    if window and window['starts_at'] <= clock:
        return 'maintenance'
    if egress_status and not egress_status[1] and monitor['state'] != 'down':
        return 'degraded'
    if monitor['maintenance_last_checked'] and monitor['state'] != 'down':
        return 'pending'
    return monitor['state']


@app.get('/api/infrastructure')
def infrastructure_list(user=Depends(require_user)):
    with db() as c:
        return infrastructure.list_servers(c,user['id'])


@app.post('/api/infrastructure',status_code=201)
def infrastructure_register(body:infrastructure.ServerInput,user=Depends(require_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return infrastructure.register(c,user['id'],body)


@app.get('/api/infrastructure/{sid}/history')
def infrastructure_history(sid:int,user=Depends(require_user)):
    with db() as c:
        return infrastructure.history(c,user['id'],sid)


@app.put('/api/infrastructure/{sid}')
def infrastructure_update(sid:int,body:infrastructure.ServerInput,user=Depends(require_csrf)):
    with db() as c:
        if not c.execute("""UPDATE infrastructure_servers SET name=?,cpu_threshold=?,memory_threshold=?,disk_threshold=?,
            bad_streak=0,good_streak=0 WHERE id=? AND user_id=?""",
            (body.name.strip(),body.cpu_threshold,body.memory_threshold,body.disk_threshold,sid,user['id'])).rowcount:
            raise HTTPException(404,'Server not found')
    return {'ok':True}


@app.post('/api/infrastructure/{sid}/rotate')
def infrastructure_rotate(sid:int,user=Depends(require_csrf)):
    token=secrets.token_urlsafe(32)
    with db() as c:
        if not c.execute('UPDATE infrastructure_servers SET token_hash=?,bad_streak=0,good_streak=0 WHERE id=? AND user_id=?',
            (hashlib.sha256(token.encode()).hexdigest(),sid,user['id'])).rowcount:
            raise HTTPException(404,'Server not found')
    return {'token':token}


@app.delete('/api/infrastructure/{sid}')
def infrastructure_delete(sid:int,user=Depends(require_csrf)):
    with db() as c:
        if not c.execute('DELETE FROM infrastructure_servers WHERE id=? AND user_id=?',(sid,user['id'])).rowcount:
            raise HTTPException(404,'Server not found')
    return {'ok':True}


@app.post('/api/agent/metrics')
async def agent_metrics(request:Request):
    match=re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})',request.headers.get('authorization',''),flags=re.IGNORECASE|re.ASCII)
    if not match:
        raise HTTPException(401,'Agent token required')
    # Cap streamed bodies too; never expose validation input or credentials.
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>65536:
            raise HTTPException(413,'Metrics too large')
    try:
        metrics=infrastructure.Metrics.model_validate_json(data)
    except ValueError:
        raise HTTPException(422,'Invalid metrics payload')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return infrastructure.ingest(c,match[1],metrics,enqueue_alert)


@app.get('/agent/linux.py')
def download_linux_agent():
    return FileResponse(ROOT/'linux_agent.py',media_type='text/plain',filename='beepnado-agent.py')


@app.get('/agent/windows.ps1')
def download_windows_agent():
    return FileResponse(ROOT/'windows_agent.ps1',media_type='text/plain',filename='beepnado-agent.ps1')


def browser_origins():
    return {v.strip().lower().rstrip('/') for v in os.getenv('BROWSER_ALLOWED_ORIGINS','').split(',') if v.strip()}


def browser_enabled():
    return (os.getenv('ENABLE_BROWSER_MONITORING','false').lower()=='true'
            and len(os.getenv('BROWSER_WORKER_TOKEN',''))>=32 and bool(browser_origins()))


def require_browser_worker(request):
    if not browser_enabled() or not hmac.compare_digest(request.headers.get('x-browser-token',''),os.getenv('BROWSER_WORKER_TOKEN','')):
        raise HTTPException(401,'Browser worker not authorized')


@app.get('/api/browser')
def browser_list(user=Depends(require_user)):
    with db() as c:
        return {'enabled':browser_enabled(),'jobs':browser_checks.list_jobs(c,user['id']),
                'limit':browser_checks.LIMITS.get(user['plan'],1),'interval_seconds':browser_checks.INTERVAL}


@app.post('/api/browser',status_code=201)
async def browser_register(body:browser_checks.BrowserInput,user=Depends(require_csrf)):
    if not browser_enabled():
        raise HTTPException(503,'Browser monitoring is not enabled by the operator')
    if not body.authorized:
        raise HTTPException(422,'Confirm authorization to monitor this public page')
    try:
        await validate_url(body.url)
        parsed=urlsplit(body.url)
        if parsed.query:
            raise ValueError('Browser monitoring does not accept query strings or credentials')
        if parsed.scheme+'://'+parsed.hostname.lower() not in browser_origins():
            raise ValueError('Origin is not approved by the browser monitoring operator')
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return {'id':browser_checks.register(c,user['id'],body)}


@app.delete('/api/browser/{jid}')
def browser_delete(jid:int,user=Depends(require_csrf)):
    with db() as c:
        browser_checks.owned(c,user['id'],jid)
        c.execute('DELETE FROM browser_jobs WHERE id=? AND user_id=?',(jid,user['id']))
    return {'ok':True}


@app.get('/api/browser/{jid}/history')
def browser_history(jid:int,user=Depends(require_user)):
    with db() as c:
        return browser_checks.history(c,user['id'],jid)


@app.get('/api/browser/{jid}/image/{sample_id}')
def browser_image(jid:int,sample_id:int,user=Depends(require_user)):
    with db() as c:
        browser_checks.owned(c,user['id'],jid)
        row=c.execute('SELECT screenshot FROM browser_samples WHERE id=? AND job_id=?',(sample_id,jid)).fetchone()
        if not row or not row['screenshot']:
            raise HTTPException(404,'Screenshot not found')
        return Response(row['screenshot'],media_type='image/jpeg',headers={'Cache-Control':'no-store'})


@app.post('/internal/browser/claim')
def browser_claim(request:Request):
    require_browser_worker(request)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return {'job':browser_checks.claim(c)}


@app.post('/internal/browser/result')
async def browser_result(request:Request):
    require_browser_worker(request)
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>65536:
            raise HTTPException(413,'Browser result too large')
    try:
        body=browser_checks.BrowserResult.model_validate_json(data)
    except ValueError:
        raise HTTPException(422,'Invalid browser result')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return browser_checks.submit(c,body)


@app.get('/api/team/workspaces')
def workspace_list(actor=Depends(require_actor)):
    with db() as c:
        return teams.workspaces(c,actor)


@app.get('/api/team/roster')
def workspace_roster(user=Depends(require_user)):
    with db() as c:
        return teams.roster(c,user['id'],user['actor_id'])


@app.put('/api/team/name')
def workspace_rename(body:teams.WorkspaceInput,request:Request,actor=Depends(actor_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user=teams.scope(c,actor,request.headers.get('x-workspace-id'))
        if user['workspace_role']!='owner':
            raise HTTPException(403,'Only the owner can rename the workspace')
        c.execute('INSERT INTO team_workspaces(owner_id,name) VALUES(?,?) ON CONFLICT(owner_id) DO UPDATE SET name=excluded.name',
                  (user['id'],body.name.strip()))
        teams.audit(c,user['id'],actor['id'],'Renamed workspace')
    return {'ok':True}


@app.post('/api/team/invite')
def workspace_invite(body:teams.InviteInput,request:Request,actor=Depends(actor_csrf)):
    throttle(request,'team-invite',str(actor['id']),10,3600)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user=teams.scope(c,actor,request.headers.get('x-workspace-id'))
        token,email=teams.invite(c,user['id'],actor['id'],body)
        link=SITE_URL+'/#invite='+token
        queued=False
        if email_ready():
            c.execute('INSERT INTO email_outbox(recipient,subject,body,created_at,next_attempt) VALUES(?,?,?,?,?)',
                      (email,'BeepNado workspace invitation','You have been invited to a BeepNado workspace. Sign in with this email address and accept within 72 hours: '+link,now(),int(time.time())))
            queued=True
    return {'link':link,'email_queued':queued}


@app.post('/api/team/accept')
def workspace_accept(body:teams.AcceptInput,actor=Depends(actor_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        return {'workspace_id':teams.accept(c,actor,body.token)}


@app.patch('/api/team/members/{member_id}')
def workspace_role(member_id:int,body:teams.RoleInput,request:Request,actor=Depends(actor_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user=teams.scope(c,actor,request.headers.get('x-workspace-id'))
        teams.change_member(c,user['id'],actor['id'],member_id,body.role)
    return {'ok':True}


@app.delete('/api/team/members/{member_id}')
def workspace_remove(member_id:int,request:Request,actor=Depends(actor_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user=teams.scope(c,actor,request.headers.get('x-workspace-id'))
        teams.change_member(c,user['id'],actor['id'],member_id)
    return {'ok':True}


@app.delete('/api/team/invites/{invite_id}')
def workspace_revoke_invite(invite_id:int,request:Request,actor=Depends(actor_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user=teams.scope(c,actor,request.headers.get('x-workspace-id'))
        role=teams.manager(c,user['id'],actor['id'])
        row=c.execute('SELECT role FROM team_invites WHERE id=? AND owner_id=?',(invite_id,user['id'])).fetchone()
        if not row:
            raise HTTPException(404,'Invitation not found')
        if role=='admin' and row['role']=='admin':
            raise HTTPException(403,'Only the owner can revoke admin invitations')
        c.execute('DELETE FROM team_invites WHERE id=?',(invite_id,))
        teams.audit(c,user['id'],actor['id'],'Revoked invitation')
    return {'ok':True}


@app.get('/api/team/audit')
def workspace_audit(user=Depends(require_user)):
    with db() as c:
        teams.manager(c,user['id'],user['actor_id'])
        return [dict(r) for r in c.execute('SELECT a.action,a.created_at,u.email AS actor FROM team_audit a LEFT JOIN users u ON u.id=a.actor_id WHERE a.owner_id=? ORDER BY a.id DESC LIMIT 100',(user['id'],))]


class ChannelInput(BaseModel):
    kind: Literal['slack','discord','telegram','teams','webhook']
    label: str = Field(min_length=1,max_length=60)
    endpoint: str = Field(min_length=10,max_length=1500)
    monitor_id:int|None=Field(default=None,gt=0)
    delay_minutes:int=Field(default=0,ge=0,le=1440)


@app.get('/api/integrations')
def list_integrations(user=Depends(require_user)):
    with db() as c:
        return [dict(row) for row in c.execute("""
            SELECT a.id,a.kind,a.label,a.created_at,a.monitor_id,a.delay_minutes,
                (SELECT MAX(o.sent_at) FROM channel_outbox o WHERE o.channel_id=a.id) AS last_delivered,
                (SELECT COUNT(*) FROM channel_outbox o WHERE o.channel_id=a.id AND o.sent_at IS NULL AND o.attempts<7) AS pending,
                (SELECT COUNT(*) FROM channel_outbox o WHERE o.channel_id=a.id AND o.sent_at IS NULL AND o.attempts>=7) AS failed
            FROM alert_channels a WHERE a.user_id=? ORDER BY a.id
        """,(user['id'],))]


@app.post('/api/integrations',status_code=201)
async def add_integration(body:ChannelInput, request:Request, user=Depends(require_csrf)):
    throttle(request,'integration-add',str(user['id']),10,600)
    if body.kind=='webhook' and os.getenv('ENABLE_GENERIC_WEBHOOKS','false').lower()!='true':
        raise HTTPException(403,'Generic webhooks are disabled by the operator')
    try:
        endpoint=integrations.endpoint(body.kind,body.endpoint)
        if body.kind=='webhook':
            await validate_url(endpoint)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT COUNT(*) FROM alert_channels WHERE user_id=?',(user['id'],)).fetchone()[0]>=5:
            raise HTTPException(403,'Maximum five notification channels per account')
        if not body.label.strip():
            raise HTTPException(422,'Enter a channel label')
        if body.monitor_id is not None and not c.execute('SELECT 1 FROM monitors WHERE id=? AND user_id=?',(body.monitor_id,user['id'])).fetchone():
            raise HTTPException(404,'Monitor not found')
        channel_id=c.execute('INSERT INTO alert_channels(user_id,kind,label,endpoint,created_at,monitor_id,delay_minutes) VALUES(?,?,?,?,?,?,?)',
            (user['id'],body.kind,body.label.strip(),endpoint,now(),body.monitor_id,body.delay_minutes)).lastrowid
    return {'id':channel_id}


@app.delete('/api/integrations/{channel_id}')
def delete_integration(channel_id:int,user=Depends(require_csrf)):
    with db() as c:
        if not c.execute('DELETE FROM alert_channels WHERE id=? AND user_id=?',(channel_id,user['id'])).rowcount:
            raise HTTPException(404,'Channel not found')
    return {'ok':True}


@app.post('/api/integrations/{channel_id}/test')
def test_integration(channel_id:int,request:Request,user=Depends(require_csrf)):
    throttle(request,'integration-test',str(user['id']),5,600)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM alert_channels WHERE id=? AND user_id=?',(channel_id,user['id'])).fetchone():
            raise HTTPException(404,'Channel not found')
        clock=int(time.time())
        c.execute('INSERT INTO channel_outbox(channel_id,subject,body,created_at,next_attempt) VALUES(?,?,?,?,?)',
                  (channel_id,'BeepNado integration test','Your notification channel is configured.',clock,clock))
    return {'queued':True}


@app.get('/api/integrations/history')
def integration_history(user=Depends(require_user)):
    with db() as c:
        return [dict(r) for r in c.execute("""SELECT o.id,a.label,a.kind,o.subject,o.created_at,o.sent_at,o.attempts,
            CASE WHEN o.sent_at IS NOT NULL THEN 'delivered' WHEN o.attempts>=7 THEN 'failed' ELSE 'pending' END AS state
            FROM channel_outbox o JOIN alert_channels a ON a.id=o.channel_id
            WHERE a.user_id=? ORDER BY o.id DESC LIMIT 50""",(user['id'],))]


@app.post('/api/incidents/{incident_id}/acknowledge')
def acknowledge_incident(incident_id:int,user=Depends(require_csrf)):
    with db() as c:
        if not c.execute("""UPDATE incidents SET acknowledged_at=COALESCE(acknowledged_at,?)
            WHERE id=? AND resolved_at IS NULL AND monitor_id IN (SELECT id FROM monitors WHERE user_id=?)""",
            (now(),incident_id,user['id'])).rowcount:
            raise HTTPException(404,'Open incident not found')
    return {'ok':True}


class AlarmPreference(BaseModel):
    armed: bool


def alarm_settings(user_id):
    with db() as c:
        row = c.execute('SELECT email_alerts_enabled,email FROM users WHERE id=?', (user_id,)).fetchone()
    return {'armed': bool(row['email_alerts_enabled']), 'email_ready': email_ready(), 'email': row['email']}


@app.get('/api/alarm-settings')
def get_alarm_settings(user=Depends(require_user)):
    result=alarm_settings(user['id'])
    if user['workspace_role']!='owner':
        result.pop('email',None)
    return result


@app.put('/api/alarm-settings')
def set_alarm_settings(body: AlarmPreference, user=Depends(require_csrf)):
    with db() as c:
        c.execute('UPDATE users SET email_alerts_enabled=? WHERE id=?', (int(body.armed), user['id']))
    return alarm_settings(user['id'])


@app.post('/api/alarm-settings/test')
def test_alarm_email(request: Request, user=Depends(require_csrf)):
    throttle(request, 'alarm-test', str(user['id']), 3, 600)
    if not email_ready():
        raise HTTPException(503, 'Email delivery is not configured')
    with db() as c:
        row = c.execute('SELECT email_alerts_enabled FROM users WHERE id=?', (user['id'],)).fetchone()
        if not row['email_alerts_enabled']:
            raise HTTPException(409, 'Arm email alerts first')
        enqueue_alert(c, user['id'], 'BeepNado test alert', 'Your BeepNado email alerts are configured. This is a test, not an outage.')
    return {'queued': True}


@app.get('/api/alarm-incidents')
def alarm_incidents(user=Depends(require_user)):
    with db() as c:
        incidents = [dict(row) for row in c.execute('''
            SELECT i.id,i.monitor_id,i.opened_at,i.opening_evidence,m.name AS monitor_name
            FROM monitors m JOIN incidents i ON i.monitor_id=m.id
            WHERE m.user_id=? AND i.resolved_at IS NULL ORDER BY i.id DESC
        ''', (user['id'],))]
    for incident in incidents:
        if incident['opening_evidence']:
            incident['opening_evidence'] = json.loads(incident['opening_evidence'])
    return {'account_id': user['id'], 'incidents': incidents}


@app.get('/api/dashboard')
def dashboard(user=Depends(require_user)):
    with db() as c:
        monitors=[dict(r) for r in c.execute('SELECT * FROM monitors WHERE user_id=? ORDER BY id DESC',(user['id'],))]
        checks=[dict(r) for r in c.execute('''SELECT ch.*,m.name monitor_name FROM checks ch JOIN monitors m ON m.id=ch.monitor_id WHERE m.user_id=? ORDER BY ch.id DESC LIMIT 150''',(user['id'],))]
        for check in checks:
            check['timings'] = json.loads(check['timings']) if check['timings'] else None
            if check['journey_trace']:
                check['journey_trace'] = json.loads(check['journey_trace'])
        incidents=[dict(r) for r in c.execute('''SELECT i.*,m.name monitor_name FROM incidents i JOIN monitors m ON m.id=i.monitor_id WHERE m.user_id=? ORDER BY i.id DESC LIMIT 30''',(user['id'],))]
        for incident in incidents:
            for key in ('opening_evidence', 'recovery_evidence'):
                if incident[key]:
                    incident[key] = json.loads(incident[key])
        notices=[dict(r) for r in c.execute('''SELECT n.id,n.kind,n.message,n.created_at,m.name monitor_name
                FROM notifications n JOIN monitors m ON m.id=n.monitor_id
                WHERE n.user_id=? ORDER BY n.id DESC LIMIT 60''',(user['id'],))]
        page=c.execute('SELECT slug,title FROM status_pages WHERE user_id=?',(user['id'],)).fetchone()
        # These are primary-check success rates, not time-weighted uptime or an SLA.
        cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat(timespec='seconds')
        samples={r['monitor_id']:r for r in c.execute('''
            SELECT ch.monitor_id, COUNT(*) AS total,
                   SUM(CASE WHEN ch.state='up' THEN 1 ELSE 0 END) AS passed
              FROM checks ch JOIN monitors m ON m.id=ch.monitor_id
             WHERE m.user_id=? AND ch.region='primary' AND ch.inconclusive=0 AND ch.checked_at>=?
             GROUP BY ch.monitor_id''',(user['id'],cutoff))}
        clock = now()
        windows = scheduled_maintenance(c, user['id'], clock)
        regional = {}
        timestamp = datetime.fromisoformat(clock)
        history_since = {
            kind: (timestamp - timedelta(seconds=3 * interval)).isoformat(timespec='seconds')
            for kind, interval in EXPIRY_INTERVALS.items()
        }
        hour_ago = (timestamp - timedelta(hours=1)).isoformat(timespec='seconds')
        for row in c.execute('''
            SELECT ch.monitor_id,ch.region,ch.checked_at,ch.state,ch.http_code,ch.response_ms,ch.error,ch.detail,
                   ch.journey_trace,ch.timings,latest.probe_kind
            FROM checks ch JOIN (
                SELECT recent.monitor_id,recent.region,MAX(recent.id) AS id,m.probe_kind
                FROM monitors m JOIN checks recent ON recent.monitor_id=m.id
                WHERE m.user_id=? AND recent.checked_at>=CASE m.probe_kind
                    WHEN 'ssl' THEN ? WHEN 'domain' THEN ? WHEN 'blacklist' THEN ?
                     WHEN 'email_health' THEN ? WHEN 'spf' THEN ? WHEN 'dkim' THEN ? WHEN 'dmarc' THEN ? WHEN 'dnssec' THEN ? ELSE ? END
                  AND recent.region NOT IN ('primary','maintenance','heartbeat')
                GROUP BY recent.monitor_id,recent.region
            ) latest ON latest.id=ch.id
            ORDER BY ch.checked_at DESC, ch.region
        ''', (user['id'], history_since['ssl'], history_since['domain'], history_since['blacklist'],
               history_since['email_health'],history_since['spf'],history_since['dkim'],history_since['dmarc'],history_since['dnssec'],hour_ago)):
            observation = dict(row)
            kind = observation.pop('probe_kind')
            observation['timings'] = json.loads(observation['timings']) if observation['timings'] else None
            if observation['journey_trace']:
                observation['journey_trace'] = json.loads(observation['journey_trace'])
            freshness = max(900, 2 * EXPIRY_INTERVALS.get(kind, 0))
            observation['stale'] = datetime.fromisoformat(row['checked_at']) < timestamp - timedelta(seconds=freshness)
            regional.setdefault(row['monitor_id'], []).append(observation)
        for mon in monitors:
            mon['maintenance'] = windows.get(mon['id'])
            mon['regional_checks'] = regional.get(mon['id'], [])
            mon['display_state'] = displayed_state(mon, mon['maintenance'], clock)
            mon.pop('heartbeat_token_hash')
            kind = mon.pop('probe_kind')
            mon['kind'] = 'heartbeat' if mon['heartbeat_interval_s'] is not None else kind
            mon['probe_config'] = json.loads(mon['probe_config'])
            if kind == 'journey':
                revision = mon['probe_config']['revision']
                mon['regional_checks'] = [check for check in mon['regional_checks']
                                          if (check['journey_trace'] or {}).get('revision') == revision]
            stat=samples.get(mon['id'])
            mon['samples_30d']=int(stat['total']) if stat else 0
            mon['success_rate_30d']=round(100*stat['passed']/stat['total'],2) if stat and stat['total'] else None
    return {'monitors':monitors,'checks':checks,'incidents':incidents,'notifications':notices,'status_page':dict(page) if page else None,'plan':user['plan'],'limit':PLAN_LIMITS[user['plan']],
            'primary_connectivity_unverified': bool(egress_status and not egress_status[1])}


class CheckRules(BaseModel):
    required_phrase: str = Field(default='', max_length=120)
    expected_status: int | None = Field(default=None, ge=200, le=599)
    max_response_ms: int | None = Field(default=None, ge=100, le=15000)

    def values(self):
        return self.required_phrase.strip(), self.expected_status, self.max_response_ms


class MonitorInput(CheckRules):
    name:str=Field(min_length=2,max_length=100)
    url:str=Field(min_length=10,max_length=1500)
    authorized:bool


def require_monitor_capacity(c, user_id):
    current = c.execute('SELECT plan FROM users WHERE id=?',(user_id,)).fetchone()
    if not current:raise HTTPException(401,'Sign in to continue')
    count=c.execute('SELECT COUNT(*) FROM monitors WHERE user_id=?',(user_id,)).fetchone()[0]
    if count>=PLAN_LIMITS[current['plan']]:raise HTTPException(403,'Plan monitor limit reached')


@app.post('/api/monitors',status_code=201)
async def add_monitor(input:MonitorInput,user=Depends(require_csrf)):
    if not input.authorized:raise HTTPException(422,'You must have authorization to monitor the website')
    try: await validate_url(input.url)
    except ValueError as e:raise HTTPException(422,str(e)) from e
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        require_monitor_capacity(c, user['id'])
        cur=c.execute(
            '''INSERT INTO monitors(user_id,name,url,created_at,required_phrase,expected_status,max_response_ms)
               VALUES(?,?,?,?,?,?,?)''',
            (user['id'], input.name.strip(), input.url.strip(), now(), *input.values()),
        )
        return {'id':cur.lastrowid,'ok':True}


async def network_target(input):
    if input.kind == 'api':
        target = input.target.strip()
        await validate_url(target)
        path = input.json_path.strip()
        if path and (not path.startswith('/') or re.search(r'~(?![01])', path)):
            raise ValueError('Enter a JSON pointer such as /status or /items/0/name')
        expected = None
        if input.expected.strip():
            if not path:
                raise ValueError('Enter a JSON pointer to compare a value')
            try:
                expected = json.loads(input.expected, parse_constant=reject_json_constant, parse_float=finite_float)
            except (ValueError, RecursionError):
                raise ValueError('Expected value must be JSON, such as "ok", true, or 42') from None
            if isinstance(expected, (dict, list)):
                raise ValueError('Compare a single JSON string, number, boolean, or null')
        return target, {'status': input.status, 'json_path': path, 'expected': expected,
                        'match': bool(input.expected.strip())}

    target = public_hostname(input.target, dns_record=input.kind == 'dns')
    if input.kind in advanced_checks.PROTOCOL_PORTS:
        ports=advanced_checks.PROTOCOL_PORTS[input.kind]
        port=input.port or ports[0]
        if port not in ports:
            raise ValueError('Use one of the supported protocol ports')
        await resolve_public(target,port)
        return target,{'port':port}
    if input.kind in advanced_checks.DNS_KINDS:
        if input.kind=='blacklist':
            if os.getenv('ENABLE_DNSBL','false').lower()!='true':
                raise ValueError('DNSBL checks require operator enablement and provider approval')
            return target,{'list':input.blacklist_list}
        selector=input.selector.strip().lower()
        if input.kind=='dkim' and not selector:
            raise ValueError('Enter a DKIM selector')
        if selector and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',selector):
            raise ValueError('Enter a single valid DKIM selector label')
        return target,{'selector':selector}
    if input.kind == 'dns':
        expected = input.expected.strip()
        if expected:
            expected = normalize_dns(input.record_type, expected)
        return target, {'record_type': input.record_type, 'expected': expected}
    if input.kind == 'domain':
        return target, {'days': input.days}
    if input.kind in ('tcp', 'udp') and input.port is None:
        raise ValueError('Enter a port between 1 and 65535')
    if input.kind == 'ssl' and input.port not in (None, 443):
        raise ValueError('SSL monitors check HTTPS port 443')
    port = input.port if input.kind in ('tcp', 'udp') else 443
    await resolve_public(target, port)
    config = {'port': port}
    if input.kind == 'ssl':
        config['days'] = input.days
    if input.kind == 'udp':
        config['payload'] = check_hex(input.payload, required=True)
        config['response_prefix'] = check_hex(input.response_prefix, required=False)
    return target, config


@app.post('/api/monitors/network', status_code=201)
async def add_network_monitor(input: NetworkInput, user=Depends(require_csrf)):
    if not input.authorized or len(input.name.strip()) < 2:
        raise HTTPException(422, 'Enter a monitor name and confirm authorization')
    try:
        target, config = await network_target(input)
    except (ValueError, OSError, TimeoutError) as exc:
        raise HTTPException(422, str(exc) or 'Public DNS lookup timed out') from exc
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        require_monitor_capacity(c, user['id'])
        cur = c.execute('''INSERT INTO monitors(user_id,name,url,created_at,probe_kind,probe_config)
                           VALUES(?,?,?,?,?,?)''',
                        (user['id'], input.name.strip(), target, now(), input.kind, json.dumps(config)))
    return {'id': cur.lastrowid, 'ok': True}


@app.post('/api/monitors/journey', status_code=201)
async def add_journey(input: JourneyInput, user=Depends(require_csrf)):
    if not input.authorized or len(input.name.strip()) < 2:
        raise HTTPException(422, 'Enter a monitor name and confirm authorization')
    try:
        origin = await journey_origin(input.origin, validate_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc) or 'Public DNS lookup timed out') from exc
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        require_monitor_capacity(c, user['id'])
        cur = c.execute('''INSERT INTO monitors(user_id,name,url,created_at,probe_kind,probe_config)
            VALUES(?,?,?,?,'journey',?)''',
            (user['id'], input.name.strip(), origin, now(), json.dumps(input.new_config())))
    return {'id': cur.lastrowid, 'ok': True}


@app.patch('/api/monitors/{monitor_id}/journey')
async def update_journey(monitor_id: int, input: JourneySettings, user=Depends(require_csrf)):
    with db() as c:
        if not c.execute("SELECT 1 FROM monitors WHERE id=? AND user_id=? AND probe_kind='journey'",
                         (monitor_id, user['id'])).fetchone():
            raise HTTPException(404, 'Monitor not found')
    lock = probe_locks.setdefault(monitor_id, asyncio.Lock())
    async with lock:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT probe_config FROM monitors WHERE id=? AND user_id=? AND probe_kind='journey'",
                            (monitor_id, user['id'])).fetchone()
            if not row:
                raise HTTPException(404, 'Monitor not found')
            config = input.new_config()
            if json.loads(row['probe_config'])['steps'] != config['steps']:
                c.execute('''UPDATE monitors SET probe_config=?,last_checked=NULL,maintenance_last_checked=NULL,
                    state='pending',consecutive_failures=0,http_code=NULL,response_ms=NULL,
                    cert_days=NULL,error=NULL,detail=NULL WHERE id=? AND user_id=?''',
                    (json.dumps(config), monitor_id, user['id']))
    return {'ok': True}


class HeartbeatInput(BaseModel):
    name: str = Field(min_length=2, max_length=100, pattern=r'\S')
    interval_minutes: int = Field(ge=1, le=43200)
    grace_minutes: int = Field(ge=0, le=1440)


@app.post('/api/monitors/heartbeat', status_code=201)
def add_heartbeat(input: HeartbeatInput, user=Depends(require_csrf)):
    token = secrets.token_urlsafe(32)
    interval, grace = input.interval_minutes * 60, input.grace_minutes * 60
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        require_monitor_capacity(c, user['id'])
        cur = c.execute('''INSERT INTO monitors
            (user_id,name,url,created_at,heartbeat_token_hash,heartbeat_interval_s,heartbeat_grace_s,heartbeat_due_at)
            VALUES (?,?,'',?,?,?,?,?)''',
            (user['id'], input.name.strip(), now(), hashlib.sha256(token.encode()).hexdigest(),
             interval, grace, int(time.time()) + interval + grace),
        )
    return {'id': cur.lastrowid, 'token': token}


@app.post('/api/monitors/{monitor_id}/heartbeat-token')
def rotate_heartbeat_token(monitor_id: int, user=Depends(require_csrf)):
    token = secrets.token_urlsafe(32)
    with db() as c:
        cur = c.execute('''UPDATE monitors SET heartbeat_token_hash=?
            WHERE id=? AND user_id=? AND heartbeat_interval_s IS NOT NULL''',
            (hashlib.sha256(token.encode()).hexdigest(), monitor_id, user['id']),
        )
        if not cur.rowcount:
            raise HTTPException(404, 'Monitor not found')
    return {'token': token}


@app.post('/api/heartbeat', status_code=204)
def receive_heartbeat(request: Request):
    auth = request.headers.get('authorization', '')
    match = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})', auth, flags=re.IGNORECASE | re.ASCII)
    if not match:
        raise HTTPException(401, 'Heartbeat bearer token required')
    digest = hashlib.sha256(match[1].encode()).hexdigest()
    with db() as c:
        # Reject invalid credentials without taking the write lock.
        if not c.execute('SELECT 1 FROM monitors WHERE heartbeat_token_hash=?', (digest,)).fetchone():
            raise HTTPException(404, 'Heartbeat monitor not found')
        c.execute('BEGIN IMMEDIATE')
        monitor = c.execute('SELECT m.* ' + ACTIVE_MONITORS + ' AND m.heartbeat_token_hash=?', (digest,)).fetchone()
        if not monitor:
            raise HTTPException(404, 'Active heartbeat monitor not found')
        clock = int(time.time())
        last = monitor['heartbeat_last_at']
        if last is not None and last > clock - 5:
            return Response(status_code=204)
        overdue = monitor['heartbeat_due_at'] < clock
        # A late arrival alone cannot prove a failed job: our ingress may have been offline.
        if monitor['state'] != 'up' or overdue or (
            clock - datetime.fromisoformat(monitor['last_checked']).timestamp() >= 60
        ):
            heartbeat_observation(c, monitor, 'up')
        c.execute('UPDATE monitors SET heartbeat_due_at=?,heartbeat_last_at=? WHERE id=?',
                  (clock + monitor['heartbeat_interval_s'] + monitor['heartbeat_grace_s'], clock, monitor['id']))
    return Response(status_code=204)


@app.patch('/api/monitors/{monitor_id}/rules')
async def update_monitor_rules(monitor_id: int, input: CheckRules, user=Depends(require_csrf)):
    with db() as c:
        if not c.execute("SELECT 1 FROM monitors WHERE id=? AND user_id=? AND heartbeat_interval_s IS NULL AND probe_kind='http'",
                         (monitor_id, user['id'])).fetchone():
            raise HTTPException(404, 'Monitor not found')
    # Let any in-flight check finish before invalidating its latest state.
    lock = probe_locks.setdefault(monitor_id, asyncio.Lock())
    async with lock:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = c.execute(
                '''SELECT required_phrase,expected_status,max_response_ms
                   FROM monitors WHERE id=? AND user_id=? AND heartbeat_interval_s IS NULL AND probe_kind='http' ''', (monitor_id, user['id']),
            ).fetchone()
            if previous is None:
                raise HTTPException(404, 'Monitor not found')
            rules = tuple(
                value if field in input.model_fields_set else previous[field]
                for field, value in zip(('required_phrase', 'expected_status', 'max_response_ms'), input.values())
            )
            if tuple(previous) != rules:
                c.execute(
                    '''UPDATE monitors SET required_phrase=?,expected_status=?,max_response_ms=?,
                       last_checked=NULL,maintenance_last_checked=NULL,state='pending',consecutive_failures=0,
                       http_code=NULL,response_ms=NULL,cert_days=NULL,error=NULL WHERE id=? AND user_id=?''',
                    (*rules, monitor_id, user['id']),
                )
    return {'ok': True}


@app.delete('/api/monitors/{monitor_id}')
def delete_monitor(monitor_id:int,user=Depends(require_csrf)):
    with db() as c:
        result=c.execute('DELETE FROM monitors WHERE id=? AND user_id=?',(monitor_id,user['id']))
        if result.rowcount==0:raise HTTPException(404,'Monitor not found')
    return {'ok':True}


@app.post('/api/monitors/{monitor_id}/check')
async def manual_check(monitor_id:int,user=Depends(require_csrf)):
    with db() as c:
        monitor=c.execute('SELECT m.heartbeat_interval_s ' + ACTIVE_MONITORS + ' AND m.id=? AND m.user_id=?',
                          (monitor_id,user['id'])).fetchone()
        if not monitor and c.execute('SELECT 1 FROM monitors WHERE id=? AND user_id=?',
                                     (monitor_id,user['id'])).fetchone():
            raise HTTPException(403,'This monitor is paused under your current plan')
    if not monitor:raise HTTPException(404,'Monitor not found')
    if monitor['heartbeat_interval_s'] is not None:
        raise HTTPException(409, 'Heartbeat monitors receive check-ins from your job')
    result = await check_monitor(monitor_id, manual=True)
    if result is None:raise HTTPException(409,'This monitor is no longer active')
    return {'ok':True,'result':result}


class MaintenanceInput(BaseModel):
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime


@app.post('/api/monitors/{monitor_id}/maintenance', status_code=201)
def schedule_maintenance(monitor_id: int, input: MaintenanceInput, user=Depends(require_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 ' + ACTIVE_MONITORS +
                         ' AND m.id=? AND m.user_id=? AND m.heartbeat_interval_s IS NULL',
                         (monitor_id, user['id'])).fetchone():
            raise HTTPException(404, 'Active website monitor not found')
        clock = datetime.fromisoformat(now())
        start = input.starts_at.astimezone(timezone.utc).replace(microsecond=0) if input.starts_at else clock
        end = input.ends_at.astimezone(timezone.utc).replace(microsecond=0)
        if not clock <= start <= clock + timedelta(days=90):
            raise HTTPException(422, 'Start now or schedule within the next 90 days')
        if not timedelta(minutes=1) <= end - start <= timedelta(days=7):
            raise HTTPException(422, 'Maintenance must last between 1 minute and 7 days')
        if c.execute('SELECT 1 FROM maintenance_windows WHERE monitor_id=? AND ends_at>?',
                     (monitor_id, clock.isoformat(timespec='seconds'))).fetchone():
            raise HTTPException(409, 'End or cancel the existing maintenance window first')
        cur = c.execute('INSERT INTO maintenance_windows(monitor_id,starts_at,ends_at) VALUES(?,?,?)',
                        (monitor_id, start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds')))
        return {'id': cur.lastrowid}


@app.delete('/api/monitors/{monitor_id}/maintenance/{window_id}')
def end_maintenance(monitor_id: int, window_id: int, user=Depends(require_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        clock = now()
        window = c.execute('''SELECT w.starts_at
            FROM maintenance_windows w JOIN monitors m ON m.id=w.monitor_id
            WHERE w.id=? AND m.id=? AND m.user_id=? AND w.ends_at>?''',
            (window_id, monitor_id, user['id'], clock)).fetchone()
        if not window:
            raise HTTPException(404, 'Active or scheduled maintenance window not found')
        if window['starts_at'] < clock:
            c.execute('UPDATE maintenance_windows SET ends_at=? WHERE id=?', (clock, window_id))
        else:
            c.execute('DELETE FROM maintenance_windows WHERE id=?', (window_id,))
    return {'ok': True}


class StatusInput(BaseModel):
    slug:str=Field(min_length=3,max_length=40,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    title:str=Field(min_length=3,max_length=70)


class ClientPageInput(StatusInput):
    monitor_ids: set[int] = Field(min_length=1, max_length=max(PLAN_LIMITS.values()))
    published: bool = False


@app.get('/api/client-pages')
def list_client_pages(response: Response, user=Depends(require_user)):
    response.headers['Cache-Control'] = 'no-store'
    pages = {}
    with db() as c:
        for row in c.execute('''SELECT p.id,p.slug,p.title,p.published,pm.monitor_id
            FROM client_pages p LEFT JOIN client_page_monitors pm ON pm.page_id=p.id
            WHERE p.user_id=? ORDER BY p.title COLLATE NOCASE,p.id,pm.monitor_id''', (user['id'],)):
            page = pages.setdefault(row['id'], {
                'id': row['id'], 'slug': row['slug'], 'title': row['title'],
                'published': bool(row['published']), 'monitor_ids': [],
            })
            if row['monitor_id'] is not None:
                page['monitor_ids'].append(row['monitor_id'])
    return list(pages.values())


def save_client_page(data, user, page_id=None):
    title = data.title.strip()
    if len(title) < 3:
        raise HTTPException(422, 'Enter a title with at least three characters')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user_id = user['id']
        if page_id is not None and not c.execute('SELECT 1 FROM client_pages WHERE id=? AND user_id=?',
                                                 (page_id, user_id)).fetchone():
            raise HTTPException(404, 'Client page unavailable')
        owned = {row['id'] for row in c.execute('SELECT id FROM monitors WHERE user_id=?', (user_id,))}
        if not data.monitor_ids <= owned:
            raise HTTPException(422, 'Choose monitors in your workspace')
        reserved = c.execute('''SELECT 1 FROM status_pages WHERE slug=?
            UNION ALL SELECT 1 FROM client_pages WHERE slug=? AND id IS NOT ?''',
            (data.slug, data.slug, page_id)).fetchone()
        if reserved:
            raise HTTPException(409, 'That address is already used')
        if page_id is None:
            count = c.execute('SELECT COUNT(*) FROM client_pages WHERE user_id=?', (user_id,)).fetchone()[0]
            limit = c.execute('SELECT plan FROM users WHERE id=?', (user_id,)).fetchone()['plan']
            if count >= PLAN_LIMITS[limit]:
                raise HTTPException(403, 'Client page limit reached for your plan')
            page_id = c.execute('INSERT INTO client_pages(user_id,slug,title,published) VALUES(?,?,?,?)',
                                (user_id, data.slug, title, data.published)).lastrowid
        else:
            c.execute('UPDATE client_pages SET slug=?,title=?,published=? WHERE id=?',
                      (data.slug, title, data.published, page_id))
            c.execute('DELETE FROM client_page_monitors WHERE page_id=?', (page_id,))
        c.executemany('INSERT INTO client_page_monitors(page_id,monitor_id) VALUES(?,?)',
                      ((page_id, monitor_id) for monitor_id in data.monitor_ids))
    return {'id': page_id}


@app.post('/api/client-pages', status_code=201)
def create_client_page(data: ClientPageInput, user=Depends(require_csrf)):
    return save_client_page(data, user)


@app.put('/api/client-pages/{page_id}')
def update_client_page(page_id: int, data: ClientPageInput, user=Depends(require_csrf)):
    return save_client_page(data, user, page_id)


@app.delete('/api/client-pages/{page_id}')
def delete_client_page(page_id: int, user=Depends(require_csrf)):
    with db() as c:
        if not c.execute('DELETE FROM client_pages WHERE id=? AND user_id=?', (page_id, user['id'])).rowcount:
            raise HTTPException(404, 'Client page unavailable')
    return {'ok': True}


@app.get('/api/client-pages/{page_id}/report')
def client_report(page_id: int, response: Response, days: Literal['7', '30'] = '30', user=Depends(require_user)):
    response.headers['Cache-Control'] = 'no-store'
    end = datetime.now(timezone.utc)
    until = end.isoformat(timespec='seconds')
    since = (end - timedelta(days=int(days))).isoformat(timespec='seconds')
    with db() as c:
        c.execute('BEGIN')
        page = c.execute('SELECT title,slug,published FROM client_pages WHERE id=? AND user_id=?',
                         (page_id, user['id'])).fetchone()
        if not page:
            raise HTTPException(404, 'Client page unavailable')
        monitors = [dict(row) for row in c.execute('''
            SELECT m.name,COUNT(ch.id) AS samples,
                   COUNT(CASE WHEN ch.inconclusive=0 THEN 1 END) AS eligible_samples,
                   COUNT(CASE WHEN ch.inconclusive=1 THEN 1 END) AS inconclusive_samples,
                   COUNT(CASE WHEN ch.state='up' AND ch.inconclusive=0 THEN 1 END) AS passed,
                   COUNT(CASE WHEN ch.state='down' AND ch.inconclusive=0 THEN 1 END) AS failed,
                   ROUND(AVG(CASE WHEN ch.state='up' THEN ch.response_ms END)) AS avg_ms,
                   (SELECT COUNT(*) FROM incidents i WHERE i.monitor_id=m.id AND i.opened_at<=?
                      AND (i.resolved_at IS NULL OR i.resolved_at>?)) AS incidents
            FROM client_page_monitors pm JOIN monitors m ON m.id=pm.monitor_id
            LEFT JOIN checks ch ON ch.monitor_id=m.id AND ch.region IN ('primary','heartbeat')
                 AND ch.checked_at>=? AND ch.checked_at<=?
            WHERE pm.page_id=? AND m.user_id=? GROUP BY m.id ORDER BY m.name,m.id
        ''', (until, since, since, until, page_id, user['id']))]
    return {'title': page['title'], 'status_path': '/s/' + page['slug'] if page['published'] else None,
            'since': since, 'until': until, 'monitors': monitors}


@app.post('/api/status-page')
def set_status(input:StatusInput,user=Depends(require_csrf)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        owner=c.execute('SELECT user_id FROM status_pages WHERE slug=?',(input.slug,)).fetchone()
        if ((owner and owner['user_id'] != user['id']) or
                c.execute('SELECT 1 FROM client_pages WHERE slug=?', (input.slug,)).fetchone()):
            raise HTTPException(409,'That address is already used')
        c.execute('''INSERT INTO status_pages(user_id,slug,title,created_at) VALUES(?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET slug=excluded.slug,title=excluded.title''',(user['id'],input.slug,input.title.strip(),now()))
    return {'ok':True,'url':'/s/'+input.slug}


def public_status_page(c, slug):
    row = c.execute('''SELECT user_id,title,NULL AS page_id FROM status_pages WHERE slug=?
        UNION ALL SELECT user_id,title,id FROM client_pages WHERE slug=? AND published=1''',
        (slug, slug)).fetchone()
    if not row:
        raise HTTPException(404, 'Status page unavailable', headers={'Cache-Control': 'no-store'})
    return row


STATUS_MONITOR_SCOPE = '''AND m.user_id=? AND (? IS NULL OR EXISTS (
    SELECT 1 FROM client_page_monitors pm WHERE pm.page_id=? AND pm.monitor_id=m.id))'''


@app.get('/api/public-status/{slug}')
def status_page(slug: str, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with db() as c:
        c.execute('BEGIN')
        row = public_status_page(c, slug)
        scope = (row['user_id'], row['page_id'], row['page_id'])
        clock = now()
        windows = scheduled_maintenance(c, row['user_id'], clock)
        mons = []
        for monitor in c.execute('SELECT m.id,m.name,m.state,m.last_checked,m.maintenance_last_checked ' +
                                 ACTIVE_MONITORS + STATUS_MONITOR_SCOPE + ' ORDER BY m.name', scope):
            window = windows.get(monitor['id'])
            mons.append({
                'name': monitor['name'],
                'state': displayed_state(monitor, window, clock),
                'last_checked': monitor['maintenance_last_checked'] or monitor['last_checked'],
                'maintenance': {'starts_at': window['starts_at'], 'ends_at': window['ends_at']} if window else None,
            })
        incidents = [dict(incident) for incident in c.execute('''
            SELECT m.name AS monitor,i.opened_at,i.resolved_at
            FROM incidents i JOIN monitors m ON m.id=i.monitor_id WHERE 1=1
        ''' + STATUS_MONITOR_SCOPE + ' ORDER BY i.id DESC LIMIT 5', scope)]
    return {'title':row['title'],'monitors':mons,'incidents':incidents,'observed_at':clock,
            'primary_connectivity_unverified': bool(egress_status and not egress_status[1])}


@app.get('/badge/{slug}.svg')
def public_badge(slug:str):
    status = status_page(slug,Response())
    states = [m['state'] for m in status['monitors']]
    if 'down' in states:
        label,color='Incident','#b91c1c'
    elif status['primary_connectivity_unverified']:
        label,color='Unverified','#855a00'
    elif 'maintenance' in states:
        label,color='Maintenance','#855a00'
    elif states and all(state=='up' for state in states):
        label,color='Operational','#087443'
    else:
        label,color='Pending','#596579'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="178" height="24" role="img" aria-label="BeepNado: {label}"><rect width="178" height="24" rx="4" fill="{color}"/><path fill="#29313d" d="M4 0h79v24H4a4 4 0 0 1-4-4V4a4 4 0 0 1 4-4"/><g fill="#fff" font-family="Verdana,sans-serif" font-size="11" text-anchor="middle"><text x="42" y="16">BeepNado</text><text x="130" y="16">{label}</text></g></svg>'
    return Response(svg,media_type='image/svg+xml',headers={'Cache-Control':'no-store'})


@app.get('/s/{slug}')
def status_html(slug:str):
    with db() as c:
        public_status_page(c, slug)
    return FileResponse(ROOT/'static'/'index.html', headers={
        'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex',
    })


def get_stripe():
    if not STRIPE_KEY or not STRIPE_SIGNING:raise HTTPException(503,'Stripe billing is not configured; free accounts still work')
    try: import stripe
    except ImportError:raise HTTPException(503,'Stripe SDK not installed')
    stripe.api_key=STRIPE_KEY
    return stripe


class CheckoutInput(BaseModel):
    plan:str
    cycle:Literal['month','year']='month'


def checkout_attempt(user_id, plan):
    # Serialize reservations across requests without holding a database lock during Stripe I/O.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user or not billing_allowed(user):
            raise HTTPException(503, 'Paid checkout is unavailable')
        if user['stripe_subscription'] and user['subscription_status'] not in ('canceled', 'incomplete_expired'):
            raise HTTPException(409, 'Manage your existing subscription in billing')
        attempt = c.execute(
            'SELECT * FROM billing_checkout WHERE user_id=? AND livemode=?',
            (user_id, STRIPE_LIVE_MODE),
        ).fetchone()
        if attempt is None:
            c.execute(
                '''INSERT INTO billing_checkout(user_id,livemode,plan,request_key,created_at,customer)
                   VALUES(?,?,?,?,?,?)''',
                (user_id, STRIPE_LIVE_MODE, plan, secrets.token_hex(24), int(time.time()), user['stripe_customer']),
            )
            attempt = c.execute(
                'SELECT * FROM billing_checkout WHERE user_id=? AND livemode=?',
                (user_id, STRIPE_LIVE_MODE),
            ).fetchone()
        return dict(user), dict(attempt)


def checkout_session(stripe, user_id, plan, cycle='month'):
    price_id=NEW_PRICE_IDS.get((plan,cycle))
    if not price_id:
        raise HTTPException(503,'This billing option is not configured')
    price=stripe.Price.retrieve(price_id)
    recurring=price.get('recurring') or {}
    expected=PLAN_MONTHLY[plan]*(10 if cycle=='year' else 1)*100
    if not price.get('active') or price.get('currency')!='usd' or price.get('unit_amount')!=expected or recurring.get('interval')!=cycle or recurring.get('interval_count')!=1 or price.get('livemode')!=STRIPE_LIVE_MODE:
        raise HTTPException(503,'Stripe price does not match the published plan')
    selection=f'{plan}:{cycle}:{price_id}'
    while True:
        user, attempt = checkout_attempt(user_id, selection)
        if not attempt['session_id']:
            break
        session = stripe.checkout.Session.retrieve(attempt['session_id'])
        if session.status == 'open':
            if attempt['plan'] == selection:
                return session.url
            session = stripe.checkout.Session.expire(attempt['session_id'])
        completed_and_canceled = (
            session.status == 'complete' and session.subscription == user['stripe_subscription']
            and user['subscription_status'] in ('canceled', 'incomplete_expired')
        )
        if session.status != 'expired' and not completed_and_canceled:
            raise HTTPException(409, 'Your last checkout is still being processed. Check your plan before trying again.')
        with db() as c:
            c.execute('DELETE FROM billing_checkout WHERE request_key=?', (attempt['request_key'],))

    if attempt['plan'] != selection:
        raise HTTPException(409, 'Retry the plan you selected first.')
    if time.time() - attempt['created_at'] >= 23 * 3600:
        raise HTTPException(409, 'This checkout attempt needs support before it can be retried.')
    params = {
        'mode': 'subscription',
        'line_items': [{'price': price_id, 'quantity': 1}],
        'success_url': SITE_URL + '/?billing=return',
        'cancel_url': SITE_URL + '/?billing=cancel',
        'client_reference_id': str(user_id),
        'subscription_data': {'metadata': {'beepnado_user_id': str(user_id)}},
    }
    if attempt['customer']:
        params['customer'] = attempt['customer']
    else:
        params['customer_email'] = user['email']
    session = stripe.checkout.Session.create(**params, idempotency_key='beepnado-' + attempt['request_key'])
    with db() as c:
        c.execute('UPDATE billing_checkout SET session_id=? WHERE request_key=?',
                  (session.id, attempt['request_key']))
    if session.status != 'open' or not session.url:
        raise HTTPException(409, 'Your last checkout is still being processed. Check your plan before trying again.')
    return session.url


@app.post('/api/billing/checkout')
def checkout(input: CheckoutInput, user=Depends(require_csrf)):
    if not billing_allowed(user):
        raise HTTPException(503, 'Paid checkout is unavailable')
    if input.plan not in ('pro', 'team', 'scale'):
        raise HTTPException(422, 'Unknown paid plan')
    stripe = get_stripe()
    try:
        return {'url': checkout_session(stripe, user['id'], input.plan, input.cycle)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, 'Unable to create Checkout session. Retry the same plan.') from exc


@app.post('/api/billing/portal')
def portal(user=Depends(require_csrf)):
    if not billing_account_allowed(user):
        raise HTTPException(503, 'Billing portal is unavailable')
    if not user['stripe_customer']:
        raise HTTPException(404, 'No paid subscription account')
    stripe = get_stripe()
    params = {'customer': user['stripe_customer'], 'return_url': SITE_URL + '/#pricing'}
    if STRIPE_LIVE_MODE:
        params['configuration'] = STRIPE_PORTAL_CONFIGURATION
    try:
        return {'url': stripe.billing_portal.Session.create(**params).url}
    except Exception as exc:
        raise HTTPException(502, 'Unable to open billing portal') from exc


SUBSCRIPTION_EVENTS = frozenset({
    'customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted',
})


@app.post('/stripe/webhook')
async def stripe_webhook(request: Request):
    stripe = get_stripe()
    payload = await request.body()
    signature = request.headers.get('stripe-signature', '')
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_SIGNING)
    except Exception as exc:
        raise HTTPException(400, 'Stripe webhook signature invalid') from exc
    if (event['type'] not in SUBSCRIPTION_EVENTS
            or event.get('livemode') != STRIPE_LIVE_MODE):
        return {'ok': True}

    if STRIPE_LIVE_MODE:
        # Stripe can deliver subscription events out of order. The single web worker
        # serializes reconciliation against the current Stripe subscription.
        async with LIVE_WEBHOOK_LOCK:
            sub = event['data']['object']
            if event['type'] != 'customer.subscription.deleted':
                with db() as c:
                    if c.execute('SELECT 1 FROM billing_events WHERE event_id=?', (event['id'],)).fetchone():
                        return {'ok': True}
                    linked = c.execute(
                        'SELECT 1 FROM users WHERE stripe_customer=? AND stripe_subscription=?',
                        (sub.get('customer'), sub.get('id')),
                    ).fetchone()
                if not linked and not (sub.get('metadata') or {}).get('beepnado_user_id'):
                    return {'ok': True}
                try:
                    sub = await asyncio.to_thread(stripe.Subscription.retrieve, sub['id'])
                except Exception as exc:
                    raise HTTPException(502, 'Unable to verify Stripe subscription') from exc
            return reconcile_subscription(event, sub)
    return reconcile_subscription(event, event['data']['object'])


def reconcile_subscription(event, sub):
    customer, subscription = sub.get('customer'), sub.get('id')
    if not customer or not subscription:
        return {'ok': True}

    uid = (sub.get('metadata') or {}).get('beepnado_user_id')
    items = (sub.get('items') or {}).get('data', [])
    price = (items[0].get('price') or {}).get('id') if len(items) == 1 else None
    plan = price_mapping().get(price)
    status = 'canceled' if event['type'] == 'customer.subscription.deleted' else sub.get('status')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM billing_events WHERE event_id=?', (event['id'],)).fetchone():
            return {'ok': True}
        user = c.execute(
            'SELECT * FROM users WHERE stripe_customer=? AND stripe_subscription=?',
            (customer, subscription),
        ).fetchone()
        if user is None and uid and str(uid).isascii() and str(uid).isdigit() and len(str(uid)) <= 18 and plan and status not in ('canceled', 'incomplete_expired'):
            user = c.execute(
                '''SELECT * FROM users WHERE id=? AND (stripe_customer IS NULL OR stripe_customer=?)
                   AND (stripe_subscription IS NULL OR subscription_status IN ('canceled', 'incomplete_expired'))''',
                (int(uid), customer),
            ).fetchone()
        if user and billing_account_allowed(user):
            if user['stripe_subscription'] == subscription and user['subscription_status'] == 'canceled':
                return {'ok': True}
            active_plan = plan if plan and status in ('active', 'trialing') else 'free'
            c.execute(
                'UPDATE users SET plan=?,subscription_status=?,stripe_customer=?,stripe_subscription=? WHERE id=?',
                (active_plan, status, customer, subscription, user['id']),
            )
            c.execute('INSERT INTO billing_events(event_id,processed_at) VALUES(?,?)', (event['id'], now()))
    return {'ok': True}


def worker_revision(row):
    return hashlib.sha256(json.dumps([row['url'],row['probe_kind'],row['probe_config'],row['required_phrase'],
        row['expected_status'],row['max_response_ms']],separators=(',',':')).encode()).hexdigest()


@app.get('/internal/targets')
def remote_targets(request:Request, typed: bool = False, journeys: bool = False):
    if not WORKER_TOKEN or len(WORKER_TOKEN)<32 or not hmac.compare_digest(request.headers.get('x-worker-token',''),WORKER_TOKEN):
        raise HTTPException(401,'Worker not authorized')
    with db() as c:
        rows = c.execute('SELECT m.id,m.url,m.probe_kind,m.probe_config,m.required_phrase,m.expected_status,m.max_response_ms ' + ACTIVE_MONITORS +
                         ' AND m.heartbeat_interval_s IS NULL')
        if typed:
            targets = [{'id': row['id'], 'url': row['url'], 'kind': row['probe_kind'],
                        'config': json.loads(row['probe_config']), 'rules': {'required_phrase':row['required_phrase'],'expected_status':row['expected_status'],'max_response_ms':row['max_response_ms']}, 'revision': worker_revision(row)} for row in rows
                       if journeys or row['probe_kind'] != 'journey']
        else:
            targets = [{'id': row['id'], 'url': row['url']} for row in rows if row['probe_kind'] == 'http']
    return {'targets': targets}


class ProbeTimings(BaseModel):
    dns_ms:int|None=Field(default=None,ge=0,le=120000)
    tcp_ms:int|None=Field(default=None,ge=0,le=120000)
    tls_ms:int|None=Field(default=None,ge=0,le=120000)
    ttfb_ms:int|None=Field(default=None,ge=0,le=120000)
    headers_ms:int|None=Field(default=None,ge=0,le=120000)
    body_ms:int|None=Field(default=None,ge=0,le=120000)
    body_bytes:int|None=Field(default=None,ge=0,le=CONTENT_LIMIT)
    total_ms:int|None=Field(default=None,ge=0,le=120000)
    route:Literal['local','public']|None=None


class WorkerResult(BaseModel):
    monitor_id:int=Field(gt=0)
    region:str=Field(min_length=2,max_length=32,pattern=r'^[a-z0-9_-]+$')
    state:str
    http_code:int|None=None
    response_ms:int|None=None
    cert_days:int|None=None
    error:str|None=None
    detail:str|None=Field(default=None,max_length=250)
    journey_trace:JourneyTrace|None=None
    revision:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    timings:ProbeTimings|None=None


@app.post('/internal/result')
def remote_result(item:WorkerResult,request:Request):
    if not WORKER_TOKEN or len(WORKER_TOKEN)<32 or not hmac.compare_digest(request.headers.get('x-worker-token',''),WORKER_TOKEN):
        raise HTTPException(401,'Worker not authorized')
    if item.state not in ('up','down','unknown') or item.region in ('primary', 'maintenance', 'heartbeat'):raise HTTPException(422,'Invalid result')
    # Keep the external observation separate from primary incident state.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        monitor = c.execute('SELECT m.* ' + ACTIVE_MONITORS +
                            ' AND m.id=? AND m.heartbeat_interval_s IS NULL', (item.monitor_id,)).fetchone()
        if not monitor:
            raise HTTPException(404)
        if monitor['probe_kind'] == 'journey':
            revision = json.loads(monitor['probe_config'])['revision']
            if not item.journey_trace or item.journey_trace.revision != revision:
                raise HTTPException(409, 'Journey settings changed. Fetch the current targets.')
        elif item.journey_trace:
            raise HTTPException(422, 'Journey evidence requires a journey monitor')
        if item.revision and not hmac.compare_digest(item.revision,worker_revision(monitor)):
            raise HTTPException(409,'Monitor configuration changed')
        result = item.model_dump()
        if item.revision and item.state=='down':
            result['detail']='Confirmed revision '+item.revision
        result['error'] = (item.error or '')[:100]
        state = 'degraded' if item.state == 'unknown' else item.state
        store_check(c, item.monitor_id, item.region, now(), state, result)
    return {'ok':True}
