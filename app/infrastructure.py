"""Account-scoped infrastructure agents with bounded metrics retention."""
import hashlib
import json
import re
import secrets
import time
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field

RETENTION=7*86400
INTERVAL=60
LIMITS={'free':1,'pro':5,'team':25,'scale':50}


class ServerInput(BaseModel):
    name:str=Field(min_length=2,max_length=100,pattern=r'\S')
    cpu_threshold:float=Field(default=90,ge=1,le=100)
    memory_threshold:float=Field(default=90,ge=1,le=100)
    disk_threshold:float=Field(default=90,ge=1,le=100)


class Disk(BaseModel):
    mount:str=Field(max_length=200)
    used_percent:float=Field(ge=0,le=100)
    total_bytes:int=Field(ge=0,le=10**18)
    free_bytes:int=Field(ge=0,le=10**18)


class Service(BaseModel):
    name:str=Field(max_length=120)
    state:str=Field(max_length=40)


class Container(BaseModel):
    name:str=Field(max_length=120)
    state:str=Field(max_length=40)
    health:str|None=Field(default=None,max_length=40)


class Metrics(BaseModel):
    platform:Literal['linux','windows']
    hostname:str=Field(max_length=120)
    cpu_percent:float|None=Field(default=None,ge=0,le=100)
    memory_percent:float=Field(ge=0,le=100)
    swap_percent:float|None=Field(default=None,ge=0,le=100)
    cpu_count:int=Field(ge=1,le=4096)
    uptime_seconds:int=Field(ge=0,le=10**10)
    load1:float|None=Field(default=None,ge=0,le=100000)
    network_rx_bytes:int|None=Field(default=None,ge=0,le=10**20)
    network_tx_bytes:int|None=Field(default=None,ge=0,le=10**20)
    disk_read_bytes:int|None=Field(default=None,ge=0,le=10**20)
    disk_write_bytes:int|None=Field(default=None,ge=0,le=10**20)
    network_rx_bps:float|None=Field(default=None,ge=0,le=10**15)
    network_tx_bps:float|None=Field(default=None,ge=0,le=10**15)
    disk_read_bps:float|None=Field(default=None,ge=0,le=10**15)
    disk_write_bps:float|None=Field(default=None,ge=0,le=10**15)
    temperature_c:float|None=Field(default=None,ge=-100,le=250)
    reboot_pending:bool|None=None
    critical_events_5m:int|None=Field(default=None,ge=0,le=100)
    processes:list[Service]=Field(default_factory=list,max_length=32)
    disks:list[Disk]=Field(default_factory=list,max_length=32)
    services:list[Service]=Field(default_factory=list,max_length=32)
    containers:list[Container]=Field(default_factory=list,max_length=64)


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS infrastructure_servers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL, last_seen INTEGER,
        cpu_threshold REAL NOT NULL, memory_threshold REAL NOT NULL, disk_threshold REAL NOT NULL,
        bad_streak INTEGER NOT NULL DEFAULT 0, good_streak INTEGER NOT NULL DEFAULT 0,
        alarm INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS infrastructure_samples(
        id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL REFERENCES infrastructure_servers(id) ON DELETE CASCADE,
        received_at INTEGER NOT NULL, metrics TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS infrastructure_samples_server ON infrastructure_samples(server_id,id DESC);
    CREATE INDEX IF NOT EXISTS infrastructure_samples_expiry ON infrastructure_samples(received_at);
    """)


def register(c,user_id,settings):
    plan=c.execute('SELECT plan FROM users WHERE id=?',(user_id,)).fetchone()['plan']
    if c.execute('SELECT COUNT(*) FROM infrastructure_servers WHERE user_id=?',(user_id,)).fetchone()[0]>=LIMITS.get(plan,1):
        raise HTTPException(403,'Server agent limit reached')
    token=secrets.token_urlsafe(32)
    sid=c.execute("""INSERT INTO infrastructure_servers(user_id,name,token_hash,created_at,cpu_threshold,memory_threshold,disk_threshold)
        VALUES(?,?,?,?,?,?,?)""",(user_id,settings.name.strip(),hashlib.sha256(token.encode()).hexdigest(),int(time.time()),
        settings.cpu_threshold,settings.memory_threshold,settings.disk_threshold)).lastrowid
    return {'id':sid,'token':token,'interval_seconds':INTERVAL}


def violations(server,metrics):
    issues=[]
    for key,label,threshold in [('cpu_percent','CPU','cpu_threshold'),('memory_percent','Memory','memory_threshold')]:
        value=getattr(metrics,key)
        if value is not None and value>=server[threshold]:
            issues.append(f'{label} {value:.1f}%')
    for disk in metrics.disks:
        if disk.used_percent>=server['disk_threshold']:
            issues.append(f'Disk {disk.mount} {disk.used_percent:.1f}%')
    for service in metrics.services+metrics.processes:
        if service.state in ('failed','inactive','deactivating'):
            issues.append(f'Service {service.name}: {service.state}')
    for container in metrics.containers:
        if container.state not in ('running','unknown') or container.health=='unhealthy':
            issues.append(f'Container {container.name}: {container.health or container.state}')
    return issues


def ingest(c,token,metrics,notify):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
        raise HTTPException(401,'Agent token required')
    server=c.execute('SELECT * FROM infrastructure_servers WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not server:
        raise HTTPException(401,'Invalid agent token')
    clock=int(time.time())
    if server['last_seen'] is not None and clock-server['last_seen']<45:
        raise HTTPException(429,'Send metrics no more than once per minute')
    # Reuse account-wide channels only; server agents are not website monitors.
    issues=violations(server,metrics)
    # Missing measurements must not clear an existing alarm.
    incomplete=(metrics.cpu_percent is None or any(s.state=='unknown' for s in metrics.services+metrics.processes)
                or any(x.state=='unknown' for x in metrics.containers))
    bad=server['bad_streak']+1 if issues else 0
    good=server['good_streak']+1 if not issues and not incomplete else 0
    if server['last_seen'] is None or clock-server['last_seen']>180:
        bad=int(bool(issues))
        good=int(not issues and not incomplete)
    alarm=server['alarm']
    if bad>=3 and not alarm:
        alarm=1
        notify(c,server['user_id'],'BeepNado server threshold: '+server['name'],'Three consecutive resource or service warnings: '+('; '.join(issues))[:1200])
    elif good>=3 and alarm:
        alarm=0
        notify(c,server['user_id'],'BeepNado server recovery: '+server['name'],'Three consecutive samples without reported threshold violations.')
    c.execute('UPDATE infrastructure_servers SET last_seen=?,bad_streak=?,good_streak=?,alarm=? WHERE id=?',
              (clock,min(bad,3),min(good,3),alarm,server['id']))
    previous=c.execute('SELECT metrics FROM infrastructure_samples WHERE server_id=? ORDER BY id DESC LIMIT 1',(server['id'],)).fetchone()
    payload=metrics.model_dump()
    payload['reboot_observed']=False
    if previous:
        old=json.loads(previous['metrics'])
        payload['reboot_observed']=metrics.uptime_seconds+60<old.get('uptime_seconds',0)
    c.execute('INSERT INTO infrastructure_samples(server_id,received_at,metrics) VALUES(?,?,?)',
              (server['id'],clock,json.dumps(payload)))
    return {'ok':True,'interval_seconds':INTERVAL}


def list_servers(c,user_id):
    clock=int(time.time())
    output=[]
    for row in c.execute("""SELECT id,name,created_at,last_seen,cpu_threshold,memory_threshold,disk_threshold,alarm
        FROM infrastructure_servers WHERE user_id=? ORDER BY id""",(user_id,)):
        item=dict(row)
        item['state']='awaiting agent' if row['last_seen'] is None else 'stale / unverified' if clock-row['last_seen']>180 else 'warning' if row['alarm'] else 'reporting'
        latest=c.execute('SELECT metrics FROM infrastructure_samples WHERE server_id=? ORDER BY id DESC LIMIT 1',(row['id'],)).fetchone()
        item['latest']=json.loads(latest['metrics']) if latest else None
        output.append(item)
    return output


def history(c,user_id,sid):
    if not c.execute('SELECT 1 FROM infrastructure_servers WHERE id=? AND user_id=?',(sid,user_id)).fetchone():
        raise HTTPException(404,'Server not found')
    return [{'received_at':r['received_at'],**json.loads(r['metrics'])} for r in c.execute(
        'SELECT received_at,metrics FROM infrastructure_samples WHERE server_id=? ORDER BY id DESC LIMIT 120',(sid,))]
