"""Page-specific branding and announcements with stable page identity."""
import hashlib
import secrets
import re
import time
import json
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field

DOMAIN_LIMITS={'free':0,'pro':1,'team':5,'scale':20}
SUB_LIMITS={'free':25,'pro':100,'team':1000,'scale':5000}
THEMES={'default','light','dark'}
COLORS={'orange','blue','green','purple','slate'}

class BrandInput(BaseModel):
    brand_name:str=Field(default='',max_length=70)
    theme:Literal['default','light','dark']='default'
    color:Literal['orange','blue','green','purple','slate']='orange'
    hide_brand:bool=False
    footer:str=Field(default='',max_length=250)
    logo_text:str=Field(default='',max_length=8)

class AnnouncementInput(BaseModel):
    title:str=Field(min_length=3,max_length=100,pattern=r'\S')
    body:str=Field(min_length=1,max_length=2000,pattern=r'\S')
    kind:Literal['incident','maintenance','update']='update'

class DomainInput(BaseModel):
    hostname:str=Field(min_length=4,max_length=253)

class SubscriberInput(BaseModel):
    email:str=Field(min_length=5,max_length=160)

class SubscriberToken(BaseModel):
    token:str=Field(min_length=43,max_length=43,pattern=r'^[A-Za-z0-9_-]{43}$')

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS status_branding(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        primary_id INTEGER UNIQUE REFERENCES status_pages(id) ON DELETE CASCADE,
        client_id INTEGER UNIQUE REFERENCES client_pages(id) ON DELETE CASCADE,
        config TEXT NOT NULL DEFAULT '{}',
        CHECK((primary_id IS NULL)!=(client_id IS NULL))
    );
    CREATE TABLE IF NOT EXISTS status_announcements(
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER NOT NULL REFERENCES status_branding(id) ON DELETE CASCADE,
        title TEXT NOT NULL, body TEXT NOT NULL, kind TEXT NOT NULL, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS status_domains(
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER NOT NULL REFERENCES status_branding(id) ON DELETE CASCADE,
        hostname TEXT NOT NULL UNIQUE, token TEXT NOT NULL, verified_at INTEGER, activated_at INTEGER,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS status_subscribers(
        id INTEGER PRIMARY KEY AUTOINCREMENT, brand_id INTEGER NOT NULL REFERENCES status_branding(id) ON DELETE CASCADE,
        email TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, unsubscribe_hash TEXT UNIQUE,
        expires_at INTEGER NOT NULL, confirmed_at INTEGER, created_at INTEGER NOT NULL,
        UNIQUE(brand_id,email)
    );
    CREATE TABLE IF NOT EXISTS status_daily_views(
        brand_id INTEGER NOT NULL REFERENCES status_branding(id) ON DELETE CASCADE,
        day INTEGER NOT NULL, views INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(brand_id,day)
    );
    CREATE INDEX IF NOT EXISTS status_announcements_brand ON status_announcements(brand_id,id DESC);
    """)

def page(c,owner,kind,pid,create=False):
    table='status_pages' if kind=='primary' else 'client_pages'
    column='primary_id' if kind=='primary' else 'client_id'
    row=c.execute(f'SELECT id,slug,title FROM {table} WHERE id=? AND user_id=?',(pid,owner)).fetchone()
    if not row:raise HTTPException(404,'Status page not found')
    if create:
        c.execute(f'INSERT OR IGNORE INTO status_branding({column}) VALUES(?)',(pid,))
    brand=c.execute(f'SELECT * FROM status_branding WHERE {column}=?',(pid,)).fetchone()
    return row,brand

def effective(config,plan):
    if plan=='free':return BrandInput().model_dump()
    data=BrandInput.model_validate(config).model_dump()
    if plan not in ('team','scale'):data['hide_brand']=False
    return data

def save(c,owner,kind,pid,body,plan):
    if plan=='free':raise HTTPException(403,'Custom branding requires a paid plan')
    if body.hide_brand and plan not in ('team','scale'):raise HTTPException(403,'White labeling requires Team or Scale')
    _,b=page(c,owner,kind,pid,True)
    c.execute('UPDATE status_branding SET config=? WHERE id=?',(body.model_dump_json(),b['id']))

def public(c,owner,client_id):
    if client_id is None:
        row=c.execute('SELECT b.* FROM status_branding b JOIN status_pages p ON p.id=b.primary_id WHERE p.user_id=?',(owner,)).fetchone()
    else:
        row=c.execute('SELECT * FROM status_branding WHERE client_id=?',(client_id,)).fetchone()
    plan=c.execute('SELECT plan FROM users WHERE id=?',(owner,)).fetchone()[0]
    data=effective(json.loads(row['config']) if row else {},plan)
    announcements=[dict(x) for x in c.execute('SELECT title,body,kind,created_at FROM status_announcements WHERE brand_id=? ORDER BY id DESC LIMIT 10',(row['id'],))] if row else []
    return data,announcements,row

def details(c,owner,kind,pid,plan):
    p,b=page(c,owner,kind,pid)
    result={'page':dict(p),'branding':effective(json.loads(b['config']) if b else {},plan),'announcements':[],'domains':[],'subscribers':0,'views':[]}
    if b:
        result['announcements']=[dict(x) for x in c.execute('SELECT id,title,body,kind,created_at FROM status_announcements WHERE brand_id=? ORDER BY id DESC LIMIT 50',(b['id'],))]
        result['domains']=[dict(x) for x in c.execute('SELECT id,hostname,token,verified_at,activated_at FROM status_domains WHERE brand_id=? ORDER BY id',(b['id'],))]
        result['subscribers']=c.execute('SELECT COUNT(*) FROM status_subscribers WHERE brand_id=? AND confirmed_at IS NOT NULL',(b['id'],)).fetchone()[0]
        result['views']=[dict(x) for x in c.execute('SELECT day,views FROM status_daily_views WHERE brand_id=? ORDER BY day DESC LIMIT 30',(b['id'],))]
    result['domain_limit']=DOMAIN_LIMITS.get(plan,0)
    result['subscriber_limit']=SUB_LIMITS.get(plan,25)
    return result

def add_domain(c,owner,kind,pid,hostname,plan):
    limit=DOMAIN_LIMITS.get(plan,0)
    count=c.execute("""SELECT COUNT(*) FROM status_domains d JOIN status_branding b ON b.id=d.brand_id
      LEFT JOIN status_pages p ON p.id=b.primary_id LEFT JOIN client_pages q ON q.id=b.client_id
      WHERE COALESCE(p.user_id,q.user_id)=?""",(owner,)).fetchone()[0]
    if count>=limit:raise HTTPException(403,'Custom domain limit reached')
    _,b=page(c,owner,kind,pid,True)
    if c.execute('SELECT 1 FROM status_domains WHERE hostname=?',(hostname,)).fetchone():
        raise HTTPException(409,'Hostname already registered')
    token=secrets.token_urlsafe(32)
    did=c.execute('INSERT INTO status_domains(brand_id,hostname,token,created_at) VALUES(?,?,?,?)',(b['id'],hostname,token,int(time.time()))).lastrowid
    return {'id':did,'hostname':hostname,'txt_name':'_beepnado.'+hostname,'txt_value':'beepnado='+token}

def domain_owned(c,owner,kind,pid,did):
    _,b=page(c,owner,kind,pid)
    row=c.execute('SELECT * FROM status_domains WHERE id=? AND brand_id=?',(did,b['id'] if b else -1)).fetchone()
    if not row:raise HTTPException(404,'Custom domain not found')
    return row

def domain_target(c,hostname):
    row=c.execute("""SELECT d.*,COALESCE(p.slug,q.slug) slug,COALESCE(p.user_id,q.user_id) owner,
        u.plan FROM status_domains d JOIN status_branding b ON b.id=d.brand_id
        LEFT JOIN status_pages p ON p.id=b.primary_id LEFT JOIN client_pages q ON q.id=b.client_id
        JOIN users u ON u.id=COALESCE(p.user_id,q.user_id)
        WHERE d.hostname=? AND d.verified_at IS NOT NULL AND d.activated_at IS NOT NULL
          AND (p.id IS NOT NULL OR q.published=1)""",(hostname,)).fetchone()
    if not row or row['verified_at']<int(time.time())-86400:return None
    earlier=c.execute("""SELECT COUNT(*) FROM status_domains d JOIN status_branding b ON b.id=d.brand_id
        LEFT JOIN status_pages p ON p.id=b.primary_id LEFT JOIN client_pages q ON q.id=b.client_id
        WHERE COALESCE(p.user_id,q.user_id)=? AND d.id<?""",(row['owner'],row['id'])).fetchone()[0]
    return row['slug'] if earlier<DOMAIN_LIMITS.get(row['plan'],0) else None

def record_view(c,brand_id):
    day=int(time.time())//86400
    c.execute('INSERT INTO status_daily_views VALUES(?,?,1) ON CONFLICT(brand_id,day) DO UPDATE SET views=views+1',(brand_id,day))

def subscribe(c,bid,email,plan):
    email=email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Enter a valid email')
    clock=int(time.time())
    row=c.execute('SELECT * FROM status_subscribers WHERE brand_id=? AND email=?',(bid,email)).fetchone()
    if row and (row['confirmed_at'] or row['created_at']>clock-600):return None
    occupied=c.execute('SELECT COUNT(*) FROM status_subscribers WHERE brand_id=? AND (confirmed_at IS NOT NULL OR expires_at>?) AND email!=?',(bid,clock,email)).fetchone()[0]
    if occupied>=SUB_LIMITS.get(plan,25):return None
    token=secrets.token_urlsafe(32)
    c.execute("""INSERT INTO status_subscribers(brand_id,email,token_hash,expires_at,created_at) VALUES(?,?,?,?,?)
      ON CONFLICT(brand_id,email) DO UPDATE SET token_hash=excluded.token_hash,expires_at=excluded.expires_at,created_at=excluded.created_at""",
      (bid,email,hashlib.sha256(token.encode()).hexdigest(),clock+86400,clock))
    return token

def confirm(c,token):
    row=c.execute('SELECT * FROM status_subscribers WHERE token_hash=? AND expires_at>? AND confirmed_at IS NULL',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
    if not row:raise HTTPException(400,'Confirmation expired or invalid')
    unsubscribe=secrets.token_urlsafe(32)
    c.execute('UPDATE status_subscribers SET confirmed_at=?,unsubscribe_hash=? WHERE id=?',(int(time.time()),hashlib.sha256(unsubscribe.encode()).hexdigest(),row['id']))
    return row['email'],unsubscribe

def unsubscribe(c,token):
    c.execute('DELETE FROM status_subscribers WHERE unsubscribe_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
