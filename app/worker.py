"""Run this separately from a different region to record independent checks.
Requires authenticated HTTPS transport and a separate WORKER_TOKEN secret.
"""
import asyncio
import json
import os
import urllib.request
import urllib.error
from .main import probe

BASE=os.environ['BEEPNADO_URL'].rstrip('/')
TOKEN=os.environ['WORKER_TOKEN']
REGION=os.environ.get('WORKER_REGION','secondary')
if not BASE.startswith('https://') or len(TOKEN)<32 or REGION=='primary':
    raise SystemExit('Use HTTPS BEEPNADO_URL, 32+ character WORKER_TOKEN and unique non-primary WORKER_REGION')

def http(path,data=None):
    body=json.dumps(data).encode() if data is not None else None
    req=urllib.request.Request(BASE+path,data=body,headers={'X-Worker-Token':TOKEN,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as resp:
        return json.load(resp)

async def main():
    while True:
        try:
            targets=await asyncio.to_thread(http,'/internal/targets')
            for target in targets.get('targets',[]):
                result=await probe(target['url'])
                await asyncio.to_thread(http,'/internal/result',{'monitor_id':target['id'],'region':REGION,**result})
        except Exception as exc:
            print('Worker connectivity error',type(exc).__name__,flush=True)
        await asyncio.sleep(int(os.environ.get('WORKER_INTERVAL','300')))

if __name__=='__main__':asyncio.run(main())
