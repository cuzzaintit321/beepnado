#!/usr/bin/env python3
"""BeepNado Linux telemetry agent. Standard library only; no remote commands."""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def proc(name):
    return Path('/proc',name).read_text()


def cpu_snapshot():
    values=list(map(int,proc('stat').splitlines()[0].split()[1:9]))
    return sum(values),values[3]+values[4]


def command(args):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=5,check=False)
        return p.stdout[:65536] if p.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):
        return None


def collect(config,previous):
    total,idle=cpu_snapshot()
    old=previous.get('cpu')
    previous['cpu']=(total,idle)
    cpu=None if not old or total<=old[0] else round(max(0,min(100,100*(1-(idle-old[1])/(total-old[0])))),2)
    mem={line.split(':')[0]:int(line.split()[1]) for line in proc('meminfo').splitlines() if len(line.split())>=2}
    memory=round(100*(1-mem['MemAvailable']/max(1,mem['MemTotal'])),2)
    swap=round(100*(1-mem['SwapFree']/mem['SwapTotal']),2) if mem['SwapTotal'] else None
    disks=[]
    for mount in config.get('mounts',['/'])[:32]:
        try:
            usage=shutil.disk_usage(mount)
            disks.append({'mount':mount,'used_percent':round(100*usage.used/max(1,usage.total),2),
                          'total_bytes':usage.total,'free_bytes':usage.free})
        except OSError:
            continue
    rx=tx=0
    for line in proc('net/dev').splitlines()[2:]:
        name,values=line.split(':',1)
        if name.strip()=='lo':
            continue
        v=values.split()
        rx+=int(v[0]);tx+=int(v[8])
    read=write=0
    for line in proc('diskstats').splitlines():
        v=line.split()
        if len(v)<14 or not Path('/sys/block',v[2]).exists() or v[2].startswith(('loop','ram','dm-','md')):
            continue
        read+=int(v[5])*512;write+=int(v[9])*512
    services=[]
    for name in config.get('services',[])[:32]:
        if not re.fullmatch(r'[A-Za-z0-9_.@:-]{1,120}',name) or name.startswith('-'):
            continue
        output=command(['systemctl','show','--property=ActiveState','--value','--',name])
        services.append({'name':name,'state':output.strip()[:40] if output else 'unknown'})
    containers=[]
    if config.get('docker_names'):
        for name in config['docker_names'][:64]:
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',name) or name.startswith('-'):
                continue
            output=command(['docker','inspect','--format','{{json .State}}','--',name])
            if output:
                try:
                    state=json.loads(output)
                    containers.append({'name':name,'state':state.get('Status','unknown'),'health':(state.get('Health') or {}).get('Status')})
                except (ValueError,AttributeError):
                    pass
    return {'platform':'linux','hostname':socket.gethostname()[:120],'cpu_percent':cpu,
            'memory_percent':max(0,min(100,memory)),'swap_percent':swap,'cpu_count':os.cpu_count() or 1,
            'uptime_seconds':int(float(proc('uptime').split()[0])),'load1':os.getloadavg()[0],
            'network_rx_bytes':rx,'network_tx_bytes':tx,'disk_read_bytes':read,'disk_write_bytes':write,
            'disks':disks,'services':services,'containers':containers}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def send(config,metrics):
    base=config['url'].rstrip('/')
    u=urlsplit(base)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path:
        raise ValueError('Configure an HTTPS origin')
    request=urllib.request.Request(base+'/api/agent/metrics',data=json.dumps(metrics).encode(),
        headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/json'},method='POST')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=15) as response:
        return json.loads(response.read(4096))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    previous={}
    while True:
        started=time.monotonic()
        try:
            send(config,collect(config,previous))
            print('BeepNado metrics accepted',flush=True)
        except Exception as exc:
            print('BeepNado delivery failed: '+type(exc).__name__,flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(max(1,60-(time.monotonic()-started)))


if __name__=='__main__':
    main()
