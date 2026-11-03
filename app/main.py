"""BeepNado: self-hosted multi-account uptime SaaS prototype.

Secure defaults for local, private testing. Public internet production deployment
requires independent security review, TLS reverse proxy, rate limits, backups and
separate worker regions. Payment entitlements change only after Stripe webhooks.
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import smtplib
from email.message import EmailMessage
import secrets
import socket
import sqlite3
import ssl
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = os.getenv('DATABASE_PATH', '/data/beepnado.sqlite3')
SESSION_SECURE = os.getenv('SESSION_SECURE', 'true').lower() == 'true'
WORKER_TOKEN = os.getenv('WORKER_TOKEN', '')
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
PRICE_IDS = {'pro': os.getenv('STRIPE_PRICE_PRO',''), 'team': os.getenv('STRIPE_PRICE_TEAM','')}
PLAN_LIMITS = {'free': 2, 'pro': 20, 'team': 100}
PLAN_INTERVALS = {'free': 300, 'pro': 60, 'team': 60}
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
        CREATE TABLE IF NOT EXISTS incidents(
          id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          opened_at TEXT NOT NULL, resolved_at TEXT, summary TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS status_pages(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
          slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notifications_by_user ON notifications(user_id,id DESC);
        CREATE TABLE IF NOT EXISTS billing_events(event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL);
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
        # Additive migration: preserve all pre-existing accounts and monitor history.
        columns={r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'email_verified_at' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN email_verified_at TEXT')


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


def enqueue_alert(c, user_id: int, subject: str, text: str):
    if not email_ready():return
    u=c.execute('SELECT email,email_verified_at FROM users WHERE id=?',(user_id,)).fetchone()
    if u and u['email_verified_at']:
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


def session_user(request: Request):
    token = request.cookies.get(COOKIE, '')
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as c:
        row = c.execute('''SELECT u.id,u.email,u.email_verified_at,u.plan,u.stripe_customer,u.subscription_status,s.csrf
                           FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token_hash=? AND s.expires_at>?''', (token_hash,now())).fetchone()
    return dict(row) if row else None


def require_user(request: Request):
    user = session_user(request)
    if not user:
        raise HTTPException(401, 'Sign in to continue')
    if not user['email_verified_at']:
        raise HTTPException(403, 'Verify your email before accessing monitoring')
    return user


def require_csrf(request: Request, user=Depends(require_user)):
    token = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(token, user['csrf']):
        raise HTTPException(403, 'Invalid CSRF token')
    return user


async def validate_url(raw):
    """Reject private targets, credentials, redirects, custom ports, DNS rebinding."""
    try:
        u = urlsplit(raw)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment or any(ord(ch)<32 for ch in raw):
            raise ValueError('Enter a public HTTP or HTTPS URL without credentials or fragments')
        if u.port not in (None, 80 if u.scheme=='http' else 443):
            raise ValueError('Only standard 80/443 ports are supported')
        host = u.hostname.lower().rstrip('.')
        if len(host)>253 or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+',host) or any(len(x)>63 or x.startswith('-') or x.endswith('-') for x in host.split('.')):
            raise ValueError('Use a valid public domain name')
        if host.endswith(('.local','.localhost','.internal','.test','.invalid','.example')):
            raise ValueError('Only real public domains can be monitored')
        try: ipaddress.ip_address(host)
        except ValueError: pass
        else: raise ValueError('Use a public domain, not an IP literal')
        port = 443 if u.scheme=='https' else 80
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM),5)
        ips = {entry[4][0] for entry in addresses}
        if not ips or not all(ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError('The domain resolves to a private or reserved address')
        ip = sorted(ips,key=lambda a:(':' in a,a))[0]
        path = u.path or '/'
        if u.query: path += '?' + u.query
        if len(path)>1500 or any(ord(x)>127 for x in path):
            raise ValueError('URL path must be short and ASCII encoded')
        return host, ip, port, path, u.scheme
    except (ValueError, TypeError, OSError, UnicodeError, TimeoutError) as exc:
        raise ValueError(str(exc)) from exc


async def probe(url):
    """Real single-region bounded DNS-pinned HTTP(S) probe, no redirect follows."""
    started=time.monotonic()
    writer=None
    try:
        host,ip,port,path,scheme=await validate_url(url)
        target=probe_connection_target(host,ip)
        context=ssl.create_default_context() if scheme=='https' else None
        reader,writer=await asyncio.wait_for(asyncio.open_connection(
            target,port,ssl=context,server_hostname=host if context else None), timeout=8)
        writer.write((f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: BeepNado/0.1\r\nConnection: close\r\n\r\n').encode('ascii'))
        await asyncio.wait_for(writer.drain(),5)
        line=await asyncio.wait_for(reader.readline(),8)
        match=re.match(rb'HTTP/1\.[01] (\d{3})\b',line)
        if not match: raise ValueError('Invalid HTTP response')
        code=int(match.group(1)); cert_days=None
        if context:
            obj=writer.get_extra_info('ssl_object')
            cert=obj.getpeercert() if obj else None
            if cert and cert.get('notAfter'):
                cert_days=int((ssl.cert_time_to_seconds(cert['notAfter'])-time.time())//86400)
        return dict(state='up' if code<400 else 'down',http_code=code,response_ms=int((time.monotonic()-started)*1000),cert_days=cert_days,error=None if code<400 else f'HTTP {code}')
    except Exception as exc:
        return dict(state='down',http_code=None,response_ms=None,cert_days=None,error=f'{type(exc).__name__}: {str(exc)[:90]}')
    finally:
        if writer:
            writer.close()
            try: await asyncio.wait_for(writer.wait_closed(),2)
            except Exception: pass


probe_locks = {}


def record_check(monitor_id, result, region='primary'):
    with db() as c:
        m=c.execute('SELECT * FROM monitors WHERE id=? AND enabled=1',(monitor_id,)).fetchone()
        if not m: raise HTTPException(404,'Monitor not found')
        failures=m['consecutive_failures']+1 if result['state']=='down' else 0
        state='down' if failures>=2 else 'degraded' if failures>0 else 'up'
        timestamp=now()
        c.execute('''UPDATE monitors SET last_checked=?,state=?,consecutive_failures=?,http_code=?,response_ms=?,cert_days=?,error=? WHERE id=?''',
          (timestamp,state,failures,result['http_code'],result['response_ms'],result['cert_days'],result['error'],monitor_id))
        c.execute('''INSERT INTO checks(monitor_id,region,checked_at,state,http_code,response_ms,cert_days,error)
                    VALUES(?,?,?,?,?,?,?,?)''',(monitor_id,region,timestamp,state,result['http_code'],result['response_ms'],result['cert_days'],result['error']))
        incident=c.execute('SELECT id FROM incidents WHERE monitor_id=? AND resolved_at IS NULL ORDER BY id DESC LIMIT 1',(monitor_id,)).fetchone()
        if state=='down' and not incident:
            c.execute('INSERT INTO incidents(monitor_id,opened_at,summary) VALUES(?,?,?)',(monitor_id,timestamp,f'{m["name"]} is not responding'))
            c.execute('INSERT INTO notifications(user_id,monitor_id,kind,message,created_at) VALUES(?,?,?,?,?)',
                      (m['user_id'],monitor_id,'outage',f'{m["name"]}: outage confirmed after two failed checks',timestamp))
            enqueue_alert(c,m['user_id'],'BeepNado outage: '+m['name'],
                          f'An outage was confirmed for {m["name"]} after two failed checks at {timestamp} UTC.\nLog in to {SITE_URL} to investigate.')
        elif state=='up' and incident:
            c.execute('UPDATE incidents SET resolved_at=? WHERE id=?',(timestamp,incident['id']))
            c.execute('INSERT INTO notifications(user_id,monitor_id,kind,message,created_at) VALUES(?,?,?,?,?)',
                      (m['user_id'],monitor_id,'recovery',f'{m["name"]}: website is responding again',timestamp))
            enqueue_alert(c,m['user_id'],'BeepNado recovery: '+m['name'],
                          f'{m["name"]} responded successfully at {timestamp} UTC.\nSee {SITE_URL} for observations.')
        return {'state':state,'checked_at':timestamp,'http_code':result['http_code'],'response_ms':result['response_ms']}


async def check_monitor(monitor_id):
    lock=probe_locks.setdefault(monitor_id,asyncio.Lock())
    async with lock:
        with db() as c:
            mon=c.execute('SELECT url FROM monitors WHERE id=? AND enabled=1',(monitor_id,)).fetchone()
        if not mon:return None
        return record_check(monitor_id,await probe(mon['url']))


async def scheduler():
    await asyncio.sleep(3)
    while True:
        try:
            due=[]; clock=datetime.now(timezone.utc)
            with db() as c:
                for row in c.execute('''SELECT m.id,m.last_checked,u.plan FROM monitors m JOIN users u ON u.id=m.user_id WHERE m.enabled=1'''):
                    wait=PLAN_INTERVALS.get(row['plan'],300)
                    if not row['last_checked'] or (clock-datetime.fromisoformat(row['last_checked'])).total_seconds()>=wait:
                        due.append(row['id'])
                c.execute('DELETE FROM checks WHERE checked_at<?',((clock-timedelta(days=30)).isoformat(timespec='seconds'),))
                c.execute('DELETE FROM sessions WHERE expires_at<?',(now(),))
                c.execute('DELETE FROM auth_tokens WHERE expires_at<?',(now(),))
                c.execute('DELETE FROM rate_limits WHERE window_start<?',(int(time.time())-86400,))
            semaphore=asyncio.Semaphore(8)
            async def run(i):
                async with semaphore:
                    try: await check_monitor(i)
                    except Exception as exc: print('Check error',i,type(exc).__name__,flush=True)
            await asyncio.gather(*(run(i) for i in due))
        except asyncio.CancelledError:raise
        except Exception as exc: print('Scheduler error',type(exc).__name__,flush=True)
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


@asynccontextmanager
async def lifespan(app):
    init_db()
    tasks=[asyncio.create_task(scheduler()), asyncio.create_task(email_delivery_scheduler())]
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
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",'Cache-Control':'no-store'})
    return response


@app.get('/api/availability')
def public_availability():
    # This endpoint intentionally reveals only launch capabilities, never credentials.
    return {'free_registration':bool(REGISTRATION_ENABLED and email_ready()),
            'billing':bool(STRIPE_KEY and STRIPE_SIGNING and all(PRICE_IDS.values())),
            'email':email_ready()}


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


@app.get('/')
def home():return FileResponse(ROOT/'static'/'index.html')


@app.get('/static/{filename}')
def static(filename:str):
    if filename not in ('site.css','app.js','favicon.svg','experience.css','experience.js'):raise HTTPException(404)
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


@app.post('/api/login')
def login(input:Credentials,request:Request,response:Response):
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
def logout(request:Request,response:Response,user=Depends(require_csrf)):
    token=request.cookies.get(COOKIE,'')
    with db() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@app.get('/api/me')
def me(request:Request):
    user=session_user(request)
    return {'signed_in':bool(user and user.get('email_verified_at')),'user':user if user and user.get('email_verified_at') else None,
            'plans':{'free':{'monitors':2,'interval':300},'pro':{'monitors':20,'interval':60},'team':{'monitors':100,'interval':60}}}


@app.get('/api/dashboard')
def dashboard(user=Depends(require_user)):
    with db() as c:
        monitors=[dict(r) for r in c.execute('SELECT * FROM monitors WHERE user_id=? ORDER BY id DESC',(user['id'],))]
        checks=[dict(r) for r in c.execute('''SELECT ch.*,m.name monitor_name FROM checks ch JOIN monitors m ON m.id=ch.monitor_id WHERE m.user_id=? ORDER BY ch.id DESC LIMIT 150''',(user['id'],))]
        incidents=[dict(r) for r in c.execute('''SELECT i.*,m.name monitor_name FROM incidents i JOIN monitors m ON m.id=i.monitor_id WHERE m.user_id=? ORDER BY i.id DESC LIMIT 30''',(user['id'],))]
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
             WHERE m.user_id=? AND ch.region='primary' AND ch.checked_at>=?
             GROUP BY ch.monitor_id''',(user['id'],cutoff))}
        for mon in monitors:
            stat=samples.get(mon['id'])
            mon['samples_30d']=int(stat['total']) if stat else 0
            mon['success_rate_30d']=round(100*stat['passed']/stat['total'],2) if stat and stat['total'] else None
    return {'monitors':monitors,'checks':checks,'incidents':incidents,'notifications':notices,'status_page':dict(page) if page else None,'plan':user['plan'],'limit':PLAN_LIMITS[user['plan']]}


class MonitorInput(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    url:str=Field(min_length=10,max_length=1500)
    authorized:bool


@app.post('/api/monitors',status_code=201)
async def add_monitor(input:MonitorInput,user=Depends(require_csrf)):
    if not input.authorized:raise HTTPException(422,'You must have authorization to monitor the website')
    try: await validate_url(input.url)
    except ValueError as e:raise HTTPException(422,str(e)) from e
    with db() as c:
        count=c.execute('SELECT COUNT(*) FROM monitors WHERE user_id=?',(user['id'],)).fetchone()[0]
        if count>=PLAN_LIMITS[user['plan']]:raise HTTPException(403,'Plan monitor limit reached')
        cur=c.execute('INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)',(user['id'],input.name.strip(),input.url.strip(),now()))
        return {'id':cur.lastrowid,'ok':True}


@app.delete('/api/monitors/{monitor_id}')
def delete_monitor(monitor_id:int,user=Depends(require_csrf)):
    with db() as c:
        result=c.execute('DELETE FROM monitors WHERE id=? AND user_id=?',(monitor_id,user['id']))
        if result.rowcount==0:raise HTTPException(404,'Monitor not found')
    return {'ok':True}


@app.post('/api/monitors/{monitor_id}/check')
async def manual_check(monitor_id:int,user=Depends(require_csrf)):
    with db() as c:
        monitor=c.execute('SELECT last_checked FROM monitors WHERE id=? AND user_id=?',(monitor_id,user['id'])).fetchone()
    if not monitor:raise HTTPException(404,'Monitor not found')
    if monitor['last_checked'] and (datetime.now(timezone.utc)-datetime.fromisoformat(monitor['last_checked'])).total_seconds()<15:
        raise HTTPException(429,'Wait 15 seconds between manual checks')
    return {'ok':True,'result':await check_monitor(monitor_id)}


class StatusInput(BaseModel):
    slug:str=Field(min_length=3,max_length=40,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    title:str=Field(min_length=3,max_length=70)


@app.post('/api/status-page')
def set_status(input:StatusInput,user=Depends(require_csrf)):
    with db() as c:
        owner=c.execute('SELECT user_id FROM status_pages WHERE slug=?',(input.slug,)).fetchone()
        if owner and owner['user_id']!=user['id']:raise HTTPException(409,'That address is already used')
        c.execute('''INSERT INTO status_pages(user_id,slug,title,created_at) VALUES(?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET slug=excluded.slug,title=excluded.title''',(user['id'],input.slug,input.title.strip(),now()))
    return {'ok':True,'url':'/s/'+input.slug}


@app.get('/api/public-status/{slug}')
def status_page(slug:str):
    with db() as c:
        row=c.execute('SELECT user_id,title FROM status_pages WHERE slug=?',(slug,)).fetchone()
        if not row:raise HTTPException(404,'Status page unavailable')
        mons=[{'name':x['name'],'state':x['state'],'last_checked':x['last_checked']} for x in c.execute('SELECT name,state,last_checked FROM monitors WHERE user_id=? AND enabled=1 ORDER BY name',(row['user_id'],))]
        incidents=[{'monitor':x['name'],'opened_at':x['opened_at'],'resolved_at':x['resolved_at']} for x in c.execute('''SELECT m.name,i.opened_at,i.resolved_at FROM incidents i JOIN monitors m ON m.id=i.monitor_id WHERE m.user_id=? ORDER BY i.id DESC LIMIT 5''',(row['user_id'],))]
    return {'title':row['title'],'monitors':mons,'incidents':incidents,'observed_at':now()}


@app.get('/s/{slug}')
def status_html(slug:str):
    with db() as c:
        if not c.execute('SELECT 1 FROM status_pages WHERE slug=?',(slug,)).fetchone():raise HTTPException(404)
    return FileResponse(ROOT/'static'/'index.html')


def get_stripe():
    if not STRIPE_KEY or not STRIPE_SIGNING:raise HTTPException(503,'Stripe billing is not configured; free accounts still work')
    try: import stripe
    except ImportError:raise HTTPException(503,'Stripe SDK not installed')
    stripe.api_key=STRIPE_KEY
    return stripe


class CheckoutInput(BaseModel):
    plan:str


@app.post('/api/billing/checkout')
def checkout(input:CheckoutInput,user=Depends(require_csrf)):
    if input.plan not in ('pro','team') or not PRICE_IDS.get(input.plan):raise HTTPException(422,'Paid plan not configured')
    stripe=get_stripe()
    if user['stripe_customer'] and user['subscription_status'] in ('active','trialing','past_due'):
        raise HTTPException(409,'Manage your existing subscription in billing')
    params={'mode':'subscription','line_items':[{'price':PRICE_IDS[input.plan],'quantity':1}],
            'success_url':SITE_URL+'/?billing=return','cancel_url':SITE_URL+'/?billing=cancel',
            'client_reference_id':str(user['id']),'metadata':{'user_id':str(user['id']),'plan':input.plan},
            'subscription_data':{'metadata':{'user_id':str(user['id']),'plan':input.plan}}}
    if user['stripe_customer']:params['customer']=user['stripe_customer']
    else: params['customer_email']=user['email']
    try:return {'url':stripe.checkout.Session.create(**params).url}
    except Exception as exc:raise HTTPException(502,'Unable to create Checkout session') from exc


@app.post('/api/billing/portal')
def portal(user=Depends(require_csrf)):
    if not user['stripe_customer']:raise HTTPException(404,'No paid subscription account')
    stripe=get_stripe()
    try:return {'url':stripe.billing_portal.Session.create(customer=user['stripe_customer'],return_url=SITE_URL+'/').url}
    except Exception as exc:raise HTTPException(502,'Unable to open billing portal') from exc


@app.post('/stripe/webhook')
async def stripe_webhook(request:Request):
    stripe=get_stripe()
    payload=await request.body()
    signature=request.headers.get('stripe-signature','')
    try:event=stripe.Webhook.construct_event(payload,signature,STRIPE_SIGNING)
    except Exception as exc:raise HTTPException(400,'Stripe webhook signature invalid') from exc
    event_id=event['id']; event_type=event['type']; obj=event['data']['object']
    with db() as c:
        if c.execute('SELECT 1 FROM billing_events WHERE event_id=?',(event_id,)).fetchone():return {'ok':True}
        if event_type=='checkout.session.completed' and obj.get('mode')=='subscription':
            # Establish linkage only; never grant paid entitlement until subscription status is verified.
            uid=obj.get('client_reference_id')
            if uid and str(uid).isdigit():
                c.execute('UPDATE users SET stripe_customer=?,stripe_subscription=? WHERE id=? AND (stripe_customer IS NULL OR stripe_customer=?)',
                          (obj.get('customer'),obj.get('subscription'),int(uid),obj.get('customer')))
        elif event_type in ('customer.subscription.created','customer.subscription.updated','customer.subscription.deleted'):
            metadata=obj.get('metadata') or {}; uid=metadata.get('user_id'); plan=metadata.get('plan')
            customer=obj.get('customer'); subscription=obj.get('id'); status_value=obj.get('status')
            if uid and str(uid).isdigit() and plan in ('pro','team') and customer:
                plan_value=plan if status_value in ('active','trialing') and event_type!='customer.subscription.deleted' else 'free'
                c.execute('''UPDATE users SET plan=?,subscription_status=?,stripe_customer=?,stripe_subscription=?
                             WHERE id=? AND (stripe_customer IS NULL OR stripe_customer=?)''',
                          (plan_value,status_value,customer,subscription,int(uid),customer))
            elif customer:
                row=c.execute('SELECT id FROM users WHERE stripe_customer=?',(customer,)).fetchone()
                if row:
                    plan_value=plan if status_value in ('active','trialing') and plan in ('pro','team') else 'free'
                    c.execute('UPDATE users SET plan=?,subscription_status=?,stripe_subscription=? WHERE id=?',
                              (plan_value,status_value,subscription,row['id']))
        c.execute('INSERT INTO billing_events(event_id,processed_at) VALUES(?,?)',(event_id,now()))
    return {'ok':True}


@app.get('/internal/targets')
def remote_targets(request:Request):
    if not WORKER_TOKEN or len(WORKER_TOKEN)<32 or not hmac.compare_digest(request.headers.get('x-worker-token',''),WORKER_TOKEN):
        raise HTTPException(401,'Worker not authorized')
    with db() as c:targets=[{'id':row['id'],'url':row['url']} for row in c.execute('SELECT id,url FROM monitors WHERE enabled=1')]
    return {'targets':targets}


class WorkerResult(BaseModel):
    monitor_id:int=Field(gt=0)
    region:str=Field(min_length=2,max_length=32,pattern=r'^[a-z0-9_-]+$')
    state:str
    http_code:int|None=None
    response_ms:int|None=None
    cert_days:int|None=None
    error:str|None=None


@app.post('/internal/result')
def remote_result(item:WorkerResult,request:Request):
    if not WORKER_TOKEN or len(WORKER_TOKEN)<32 or not hmac.compare_digest(request.headers.get('x-worker-token',''),WORKER_TOKEN):
        raise HTTPException(401,'Worker not authorized')
    if item.state not in ('up','down') or item.region=='primary':raise HTTPException(422,'Invalid result')
    # Regional measurements are stored separately; they cannot create/close primary incidents.
    with db() as c:
        if not c.execute('SELECT 1 FROM monitors WHERE id=? AND enabled=1',(item.monitor_id,)).fetchone():raise HTTPException(404)
        c.execute('INSERT INTO checks(monitor_id,region,checked_at,state,http_code,response_ms,cert_days,error) VALUES(?,?,?,?,?,?,?,?)',
                  (item.monitor_id,item.region,now(),item.state,item.http_code,item.response_ms,item.cert_days,(item.error or '')[:100]))
    return {'ok':True}
