"""Owner-backed shared workspaces. Existing user-owned resources stay in place."""
import hashlib
import re
import secrets
import time
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field

LIMITS={'free':1,'pro':3,'team':15,'scale':50}
ROLES={'admin','operator','viewer'}
INVITE_TTL=72*3600


class InviteInput(BaseModel):
    email:str=Field(min_length=5,max_length=160)
    role:Literal['admin','operator','viewer']


class AcceptInput(BaseModel):
    token:str=Field(min_length=43,max_length=43,pattern=r'^[A-Za-z0-9_-]{43}$')


class RoleInput(BaseModel):
    role:Literal['admin','operator','viewer']


class WorkspaceInput(BaseModel):
    name:str=Field(min_length=2,max_length=100,pattern=r'\S')


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS team_workspaces(
        owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS team_members(
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        member_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
        joined_at INTEGER NOT NULL,
        PRIMARY KEY(owner_id,member_id), CHECK(owner_id!=member_id)
    );
    CREATE TABLE IF NOT EXISTS team_invites(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        email TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
        token_hash TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL,
        UNIQUE(owner_id,email)
    );
    CREATE TABLE IF NOT EXISTS team_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        action TEXT NOT NULL, created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS team_audit_owner ON team_audit(owner_id,id DESC);
    """)


def audit(c,owner,actor,action):
    c.execute('INSERT INTO team_audit(owner_id,actor_id,action,created_at) VALUES(?,?,?,?)',
              (owner,actor,action[:250],int(time.time())))


def limit(c,owner):
    row=c.execute('SELECT plan FROM users WHERE id=?',(owner,)).fetchone()
    return LIMITS.get(row[0],1) if row else 1


def role_for(c,owner,actor):
    if owner==actor:
        return 'owner'
    row=c.execute('SELECT role FROM team_members WHERE owner_id=? AND member_id=?',(owner,actor)).fetchone()
    if not row:
        return None
    # Keep oldest seats on downgrade; no data movement or deletion.
    earlier=c.execute('SELECT COUNT(*) FROM team_members WHERE owner_id=? AND (joined_at<(SELECT joined_at FROM team_members WHERE owner_id=? AND member_id=?) OR (joined_at=(SELECT joined_at FROM team_members WHERE owner_id=? AND member_id=?) AND member_id<?))',
                      (owner,owner,actor,owner,actor,actor)).fetchone()[0]
    return row['role'] if earlier<limit(c,owner)-1 else None


def scope(c,actor,requested):
    if requested in ('',None):
        owner=actor['id']
    elif isinstance(requested,str) and re.fullmatch(r'[1-9][0-9]{0,17}',requested):
        owner=int(requested)
    else:
        raise HTTPException(400,'Invalid workspace')
    role=role_for(c,owner,actor['id'])
    if not role:
        raise HTTPException(403,'Workspace access unavailable')
    row=c.execute('SELECT u.plan,w.name FROM users u LEFT JOIN team_workspaces w ON w.owner_id=u.id WHERE u.id=? AND u.email_verified_at IS NOT NULL',(owner,)).fetchone()
    if not row:
        raise HTTPException(403,'Workspace access unavailable')
    out=dict(actor,actor_id=actor['id'],workspace_id=owner,workspace_role=role,workspace_name=row['name'] or ('Personal workspace' if role=='owner' else 'Shared workspace'))
    out['id']=owner
    out['plan']=row['plan']
    if role!='owner':
        out['stripe_customer']=None
        out['subscription_status']=None
    return out


def workspaces(c,actor):
    own=scope(c,actor,None)
    rows=[{'id':own['workspace_id'],'name':own['workspace_name'],'role':'owner','plan':own['plan']}]
    for member in c.execute('SELECT owner_id FROM team_members WHERE member_id=? ORDER BY owner_id',(actor['id'],)):
        if role_for(c,member['owner_id'],actor['id']):
            shared=scope(c,actor,str(member['owner_id']))
            rows.append({'id':shared['workspace_id'],'name':shared['workspace_name'],'role':shared['workspace_role'],'plan':shared['plan']})
    return rows


def can_write(role,method,path):
    if role=='owner':
        return True
    if role=='viewer':
        return False
    if method=='POST' and (re.fullmatch(r'/api/monitors/[0-9]+/check',path) or re.fullmatch(r'/api/incidents/[0-9]+/acknowledge',path)):
        return True
    if role!='admin':
        return False
    # Explicit allowlist; new write APIs are denied until reviewed.
    if method=='POST' and path in ('/api/monitors','/api/monitors/network','/api/monitors/journey','/api/infrastructure','/api/browser','/api/integrations','/api/client-pages','/api/status-page'):
        return True
    patterns={
        'POST':[r'/api/monitors/[0-9]+/maintenance',r'/api/integrations/[0-9]+/test'],
        'PATCH':[r'/api/monitors/[0-9]+/(rules|journey)'],
        'PUT':[r'/api/infrastructure/[0-9]+',r'/api/client-pages/[0-9]+'],
        'DELETE':[r'/api/monitors/[0-9]+',r'/api/monitors/[0-9]+/maintenance/[0-9]+',r'/api/infrastructure/[0-9]+',r'/api/browser/[0-9]+',r'/api/integrations/[0-9]+',r'/api/client-pages/[0-9]+']
    }
    return any(re.fullmatch(p,path) for p in patterns.get(method,[]))


def manager(c,owner,actor):
    role=role_for(c,owner,actor)
    if role not in ('owner','admin'):
        raise HTTPException(403,'Workspace manager required')
    return role


def invite(c,owner,actor,body):
    role=manager(c,owner,actor)
    if role=='admin' and body.role=='admin':
        raise HTTPException(403,'Only the owner can appoint admins')
    email=body.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise HTTPException(422,'Enter a valid email')
    if c.execute('SELECT 1 FROM users u WHERE u.email=? AND (u.id=? OR u.id IN (SELECT member_id FROM team_members WHERE owner_id=?))',(email,owner,owner)).fetchone():
        raise HTTPException(409,'That address is already a workspace member')
    clock=int(time.time())
    existing=c.execute('SELECT role FROM team_invites WHERE owner_id=? AND email=?',(owner,email)).fetchone()
    if existing and role=='admin' and existing['role']=='admin':
        raise HTTPException(403,'Only the owner can replace an admin invitation')
    occupied=c.execute('SELECT COUNT(*) FROM team_members WHERE owner_id=?',(owner,)).fetchone()[0]
    pending=c.execute('SELECT COUNT(*) FROM team_invites WHERE owner_id=? AND expires_at>? AND email!=?',(owner,clock,email)).fetchone()[0]
    if occupied+pending>=limit(c,owner)-1:
        raise HTTPException(403,'Workspace seat limit reached')
    token=secrets.token_urlsafe(32)
    c.execute("""INSERT INTO team_invites(owner_id,email,role,token_hash,expires_at) VALUES(?,?,?,?,?)
        ON CONFLICT(owner_id,email) DO UPDATE SET role=excluded.role,token_hash=excluded.token_hash,expires_at=excluded.expires_at""",
        (owner,email,body.role,hashlib.sha256(token.encode()).hexdigest(),clock+INVITE_TTL))
    audit(c,owner,actor,'Invited '+email+' as '+body.role)
    return token,email


def accept(c,actor,token):
    row=c.execute('SELECT * FROM team_invites WHERE token_hash=? AND expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
    if not row or row['email']!=actor['email'].lower():
        raise HTTPException(400,'Invitation invalid, expired or addressed to another account')
    owner=row['owner_id']
    if owner==actor['id']:
        raise HTTPException(409,'Already the workspace owner')
    if c.execute('SELECT 1 FROM team_members WHERE owner_id=? AND member_id=?',(owner,actor['id'])).fetchone():
        raise HTTPException(409,'Already a member')
    if c.execute('SELECT COUNT(*) FROM team_members WHERE owner_id=?',(owner,)).fetchone()[0]>=limit(c,owner)-1:
        raise HTTPException(403,'Workspace seat limit reached')
    c.execute('INSERT INTO team_members VALUES(?,?,?,?)',(owner,actor['id'],row['role'],int(time.time())))
    c.execute('DELETE FROM team_invites WHERE id=?',(row['id'],))
    audit(c,owner,actor['id'],'Accepted workspace invitation')
    return owner


def roster(c,owner,actor):
    role=manager(c,owner,actor)
    return {'role':role,'limit':limit(c,owner),
            'members':[{'id':r['id'],'email':r['email'],'role':r['role'],'active':bool(role_for(c,owner,r['id']))} for r in c.execute(
                'SELECT u.id,u.email,m.role FROM team_members m JOIN users u ON u.id=m.member_id WHERE m.owner_id=? ORDER BY m.joined_at,m.member_id',(owner,))],
            'invites':[dict(r) for r in c.execute('SELECT id,email,role,expires_at FROM team_invites WHERE owner_id=? AND expires_at>? ORDER BY id',(owner,int(time.time())))]}


def change_member(c,owner,actor,member,role=None):
    acting=manager(c,owner,actor)
    existing=c.execute('SELECT role FROM team_members WHERE owner_id=? AND member_id=?',(owner,member)).fetchone()
    if not existing:
        raise HTTPException(404,'Member not found')
    if acting=='admin' and (existing['role']=='admin' or role=='admin'):
        raise HTTPException(403,'Only the owner can manage admins')
    if role:
        c.execute('UPDATE team_members SET role=? WHERE owner_id=? AND member_id=?',(role,owner,member))
    else:
        c.execute('DELETE FROM team_members WHERE owner_id=? AND member_id=?',(owner,member))
    audit(c,owner,actor,('Changed member role to '+role if role else 'Removed member')+' (account '+str(member)+')')
