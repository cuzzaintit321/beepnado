"""Private, leased synthetic browser jobs. Browser execution is external."""
import base64
import binascii
import hashlib
import json
import secrets
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

LIMITS={'free':1,'pro':3,'team':10,'scale':20}
INTERVAL=900
LEASE=180
RETENTION=7*86400
MAX_JPEG=40000


class BrowserInput(BaseModel):
    name:str=Field(min_length=2,max_length=100,pattern=r'\S')
    url:str=Field(min_length=8,max_length=1000)
    viewport:Literal['desktop','mobile']='desktop'
    required_text:str=Field(default='',max_length=120)
    authorized:bool


class BrowserResult(BaseModel):
    job_id:int=Field(gt=0)
    lease:str=Field(min_length=43,max_length=43,pattern=r'^[A-Za-z0-9_-]{43}$')
    state:Literal['observed','failed','unknown']
    status:int|None=Field(default=None,ge=100,le=599)
    load_ms:int|None=Field(default=None,ge=0,le=120000)
    fcp_ms:int|None=Field(default=None,ge=0,le=120000)
    lcp_ms:int|None=Field(default=None,ge=0,le=120000)
    cls:float|None=Field(default=None,ge=0,le=100)
    js_errors:int=Field(default=0,ge=0,le=10000)
    failed_resources:int=Field(default=0,ge=0,le=10000)
    text_found:bool|None=None
    screenshot:str|None=Field(default=None,max_length=54000)
    fingerprint:list[int]|None=Field(default=None,min_length=256,max_length=256)
    note:Literal['navigation_error','worker_error','timeout','capture_complete','capture_limited']='capture_complete'


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS browser_jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,url TEXT NOT NULL,viewport TEXT NOT NULL,required_text TEXT NOT NULL,
        created_at INTEGER NOT NULL,next_due INTEGER NOT NULL,lease_hash TEXT,lease_until INTEGER
    );
    CREATE TABLE IF NOT EXISTS browser_samples(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES browser_jobs(id) ON DELETE CASCADE,
        checked_at INTEGER NOT NULL,metrics TEXT NOT NULL,screenshot BLOB,fingerprint TEXT
    );
    CREATE INDEX IF NOT EXISTS browser_samples_job ON browser_samples(job_id,id DESC);
    CREATE INDEX IF NOT EXISTS browser_samples_expiry ON browser_samples(checked_at);
    """)


def register(c,uid,body):
    plan=c.execute('SELECT plan FROM users WHERE id=?',(uid,)).fetchone()[0]
    if c.execute('SELECT COUNT(*) FROM browser_jobs WHERE user_id=?',(uid,)).fetchone()[0]>=LIMITS.get(plan,1):
        raise HTTPException(403,'Browser monitor limit reached')
    clock=int(time.time())
    return c.execute('INSERT INTO browser_jobs(user_id,name,url,viewport,required_text,created_at,next_due) VALUES(?,?,?,?,?,?,?)',
        (uid,body.name.strip(),body.url.strip(),body.viewport,body.required_text,clock,clock)).lastrowid


def claim(c):
    clock=int(time.time())
    rows=c.execute("""SELECT b.*,u.plan FROM browser_jobs b JOIN users u ON u.id=b.user_id
        WHERE next_due<=? AND (lease_until IS NULL OR lease_until<=?) ORDER BY next_due,id""",(clock,clock))
    for row in rows:
        earlier=c.execute('SELECT COUNT(*) FROM browser_jobs WHERE user_id=? AND id<?',(row['user_id'],row['id'])).fetchone()[0]
        if earlier>=LIMITS.get(row['plan'],1):
            continue
        token=secrets.token_urlsafe(32)
        c.execute('UPDATE browser_jobs SET lease_hash=?,lease_until=? WHERE id=?',
                  (hashlib.sha256(token.encode()).hexdigest(),clock+LEASE,row['id']))
        return {k:row[k] for k in ('id','url','viewport','required_text')}|{'lease':token}
    return None


def submit(c,body):
    clock=int(time.time())
    row=c.execute('SELECT * FROM browser_jobs WHERE id=?',(body.job_id,)).fetchone()
    if not row or not row['lease_hash'] or row['lease_until']<clock or not secrets.compare_digest(row['lease_hash'],hashlib.sha256(body.lease.encode()).hexdigest()):
        raise HTTPException(409,'Invalid or expired browser job lease')
    image=None
    if body.screenshot:
        try:
            image=base64.b64decode(body.screenshot,validate=True)
        except (ValueError,binascii.Error):
            raise HTTPException(422,'Invalid screenshot') from None
        if len(image)>MAX_JPEG or not image.startswith(b'\xff\xd8\xff') or not image.endswith(b'\xff\xd9'):
            raise HTTPException(422,'Screenshot must be bounded JPEG')
    fp=body.fingerprint
    if fp is not None and (image is None or any(v<0 or v>255 for v in fp)):
        raise HTTPException(422,'Invalid visual fingerprint')
    metrics=body.model_dump(exclude={'job_id','lease','screenshot','fingerprint'})
    metrics['visual_difference_percent']=None
    if fp is not None and body.state=='observed':
        previous=c.execute('SELECT fingerprint FROM browser_samples WHERE job_id=? AND fingerprint IS NOT NULL ORDER BY id DESC LIMIT 1',(body.job_id,)).fetchone()
        if previous:
            old=json.loads(previous['fingerprint'])
            metrics['visual_difference_percent']=round(sum(abs(a-b) for a,b in zip(old,fp))/(256*255)*100,2)
    c.execute('INSERT INTO browser_samples(job_id,checked_at,metrics,screenshot,fingerprint) VALUES(?,?,?,?,?)',
              (body.job_id,clock,json.dumps(metrics),image,json.dumps(fp) if fp is not None and body.state=='observed' else None))
    c.execute('UPDATE browser_jobs SET next_due=?,lease_hash=NULL,lease_until=NULL WHERE id=?',(clock+INTERVAL,body.job_id))
    return {'ok':True}


def owned(c,uid,jid):
    if not c.execute('SELECT 1 FROM browser_jobs WHERE id=? AND user_id=?',(jid,uid)).fetchone():
        raise HTTPException(404,'Browser monitor not found')


def history(c,uid,jid):
    owned(c,uid,jid)
    return [{'id':r['id'],'checked_at':r['checked_at'],'has_screenshot':bool(r['has_screenshot']),**json.loads(r['metrics'])}
            for r in c.execute('SELECT id,checked_at,metrics,screenshot IS NOT NULL AS has_screenshot FROM browser_samples WHERE job_id=? ORDER BY id DESC LIMIT 48',(jid,))]


def list_jobs(c,uid):
    jobs=[]
    for r in c.execute('SELECT id,name,url,viewport,created_at,next_due FROM browser_jobs WHERE user_id=? ORDER BY id',(uid,)):
        item=dict(r)
        samples=history(c,uid,r['id'])
        item['latest']=samples[0] if samples else None
        jobs.append(item)
    return jobs
