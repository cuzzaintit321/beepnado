"""Versioned, workspace-bound API credentials and narrow response projections."""
import hashlib
import secrets
import time
import re
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field
from . import teams

SCOPES={'monitors:read','monitors:write','checks:read','checks:run','incidents:read','incidents:write','infrastructure:read','status:read'}
LIMITS={'free':1,'pro':3,'team':10,'scale':25}
RATES={'free':30,'pro':60,'team':120,'scale':240}

class KeyInput(BaseModel):
    name:str=Field(min_length=2,max_length=80,pattern=r'\S')
    scopes:set[Literal['monitors:read','monitors:write','checks:read','checks:run','incidents:read','incidents:write','infrastructure:read','status:read']]=Field(min_length=1,max_length=8)
    expires_days:Literal[7,30,90,365]=90

def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS management_keys(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name TEXT NOT NULL, prefix TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
      scopes TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
      last_used INTEGER, revoked_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS management_keys_owner ON management_keys(owner_id,id);
    CREATE TABLE IF NOT EXISTS management_rate(
      owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      window INTEGER NOT NULL, count INTEGER NOT NULL
    );
    """)

def create(c,owner,actor,body):
    # Only the owner may issue durable automation credentials.
    if owner!=actor:raise HTTPException(403,'Only the workspace owner can create API keys')
    plan=c.execute('SELECT plan FROM users WHERE id=?',(owner,)).fetchone()[0]
    clock=int(time.time())
    count=c.execute('SELECT COUNT(*) FROM management_keys WHERE owner_id=? AND revoked_at IS NULL AND expires_at>?',(owner,clock)).fetchone()[0]
    if count>=LIMITS.get(plan,1):raise HTTPException(403,'Active API key limit reached')
    token='bn_live_'+secrets.token_urlsafe(32)
    prefix=token[:16]
    kid=c.execute('INSERT INTO management_keys(owner_id,actor_id,name,prefix,token_hash,scopes,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
        (owner,actor,body.name.strip(),prefix,hashlib.sha256(token.encode()).hexdigest(),','.join(sorted(body.scopes)),clock,clock+body.expires_days*86400)).lastrowid
    teams.audit(c,owner,actor,'Created management API key '+str(kid))
    return {'id':kid,'token':token,'prefix':prefix,'expires_at':clock+body.expires_days*86400}

def list_keys(c,owner):
    return [dict(r,scopes=r['scopes'].split(',')) for r in c.execute(
        'SELECT id,name,prefix,scopes,created_at,expires_at,last_used,revoked_at FROM management_keys WHERE owner_id=? ORDER BY id DESC LIMIT 100',(owner,))]

def revoke(c,owner,actor,kid):
    if owner!=actor:raise HTTPException(403,'Workspace owner required')
    if not c.execute('UPDATE management_keys SET revoked_at=COALESCE(revoked_at,?) WHERE id=? AND owner_id=?',(int(time.time()),kid,owner)).rowcount:
        raise HTTPException(404,'API key not found')
    teams.audit(c,owner,actor,'Revoked management API key '+str(kid))

def authenticate(c,authorization,scope):
    match=re.fullmatch(r'Bearer (bn_live_[A-Za-z0-9_-]{43})',authorization,re.ASCII)
    if not match:raise HTTPException(401,'Management API key required',headers={'WWW-Authenticate':'Bearer'})
    clock=int(time.time())
    row=c.execute("""SELECT k.*,u.plan,u.email_verified_at FROM management_keys k
      JOIN users u ON u.id=k.owner_id WHERE k.token_hash=? AND k.revoked_at IS NULL AND k.expires_at>?""",
      (hashlib.sha256(match[1].encode()).hexdigest(),clock)).fetchone()
    if not row or not row['email_verified_at'] or row['actor_id']!=row['owner_id']:
        raise HTTPException(401,'Invalid or expired API key',headers={'WWW-Authenticate':'Bearer'})
    owner=row['owner_id']
    earlier=c.execute('SELECT COUNT(*) FROM management_keys WHERE owner_id=? AND id<? AND revoked_at IS NULL AND expires_at>?',(owner,row['id'],clock)).fetchone()[0]
    if earlier>=LIMITS.get(row['plan'],1):raise HTTPException(403,'API key paused under current plan')
    if scope not in row['scopes'].split(','):raise HTTPException(403,'API key lacks required scope: '+scope)
    window=clock//60
    rate=c.execute('SELECT window,count FROM management_rate WHERE owner_id=?',(owner,)).fetchone()
    if rate and rate['window']==window and rate['count']>=RATES.get(row['plan'],30):
        raise HTTPException(429,'Workspace API rate limit reached',headers={'Retry-After':str(60-clock%60)})
    c.execute("""INSERT INTO management_rate VALUES(?,?,1) ON CONFLICT(owner_id) DO UPDATE SET
       count=CASE WHEN management_rate.window=excluded.window THEN management_rate.count+1 ELSE 1 END,window=excluded.window""",(owner,window))
    c.execute('UPDATE management_keys SET last_used=? WHERE id=?',(clock,row['id']))
    return {'id':owner,'actor_id':owner,'key_id':row['id'],'plan':row['plan'],'workspace_role':'owner'}

def owned_monitor(c,owner,mid):
    if not c.execute('SELECT 1 FROM monitors WHERE id=? AND user_id=?',(mid,owner)).fetchone():
        raise HTTPException(404,'Monitor not found')

# Explicit projections exclude hashes, credentials, request configuration and internal evidence.
MONITOR_COLUMNS="m.id,m.name,m.url,m.probe_kind,m.state,m.enabled,m.created_at,m.last_checked,m.http_code,m.response_ms,m.error,m.heartbeat_interval_s"
def monitor(row):
    item=dict(row)
    item['kind']='heartbeat' if item.pop('heartbeat_interval_s') is not None else item['probe_kind']
    item.pop('probe_kind')
    item['enabled']=bool(item['enabled'])
    return item

def monitors(c,owner,after,limit):
    rows=c.execute('SELECT '+MONITOR_COLUMNS+' FROM monitors m WHERE m.user_id=? AND m.id>? ORDER BY m.id LIMIT ?',(owner,after,limit+1)).fetchall()
    return {'items':[monitor(r) for r in rows[:limit]],'next_after':rows[limit-1]['id'] if len(rows)>limit else None}

def one_monitor(c,owner,mid):
    row=c.execute('SELECT '+MONITOR_COLUMNS+' FROM monitors m WHERE m.user_id=? AND m.id=?',(owner,mid)).fetchone()
    if not row:raise HTTPException(404,'Monitor not found')
    return monitor(row)

def checks(c,owner,mid,before,limit):
    owned_monitor(c,owner,mid)
    rows=c.execute("""SELECT id,checked_at,region,state,http_code,response_ms,error,detail,inconclusive
      FROM checks WHERE monitor_id=? AND id<? ORDER BY id DESC LIMIT ?""",(mid,before,limit+1)).fetchall()
    return {'items':[dict(r) for r in rows[:limit]],'next_before':rows[limit-1]['id'] if len(rows)>limit else None}

def incidents(c,owner,before,limit):
    rows=c.execute("""SELECT i.id,i.monitor_id,m.name AS monitor_name,i.opened_at,i.resolved_at,i.acknowledged_at
      FROM incidents i JOIN monitors m ON m.id=i.monitor_id WHERE m.user_id=? AND i.id<? ORDER BY i.id DESC LIMIT ?""",(owner,before,limit+1)).fetchall()
    return {'items':[dict(r) for r in rows[:limit]],'next_before':rows[limit-1]['id'] if len(rows)>limit else None}
