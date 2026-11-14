"""Read-only mail/file protocol and public DNS policy checks."""
import asyncio
import ipaddress
import re
import ssl
from urllib.parse import urlencode

from .network_checks import public_address, resolve_public, result

PROTOCOL_PORTS={'smtp':(25,587,465),'imap':(143,993),'pop3':(110,995),'ftp':(21,990),'sftp':(22,)}
DNS_KINDS={'email_health','spf','dkim','dmarc','dnssec','blacklist'}
# Public DNSBL services are operator-selected; no arbitrary zones or default
# commercial-list traffic. Providers may require a subscription or local resolver.
DNSBL_ZONES={'spamhaus_zen':'zen.spamhaus.org','spamhaus_dbl':'dbl.spamhaus.org'}


async def protocol(kind,host,port):
    if port not in PROTOCOL_PORTS[kind]:
        raise ValueError('Unsupported protocol port')
    ip=await resolve_public(host,port)
    implicit=port in (465,993,995,990)
    ctx=ssl.create_default_context() if implicit else None
    async with asyncio.timeout(8):
        reader,writer=await asyncio.open_connection(ip,port,ssl=ctx,server_hostname=host if implicit else None,limit=4096)
        try:
            # No credentials, mail submission, file transfer or remote commands.
            # SSH/SFTP availability is SSH identification only, not subsystem authentication.
            line=await reader.readline()
            if not line or len(line)>2048:
                return result(error='Missing or oversized protocol greeting')
            expected={'smtp':rb'^220[ -]','ftp':rb'^220[ -]','imap':rb'^\* OK\b',
                      'pop3':rb'^\+OK\b','sftp':rb'^SSH-2\.0-'}[kind]
            if not re.match(expected,line,re.I):
                return result(error='Unexpected '+kind.upper()+' greeting')
            return result(detail=kind.upper()+' greeting accepted'+(' (verified TLS)' if implicit else ''))
        finally:
            writer.close()


async def dns_query(host,typ,get_json,**params):
    data=await get_json('https://dns.google/resolve?'+urlencode({'name':host,'type':typ,**params}))
    if data.get('TC') or data.get('Status') not in (0,3):
        raise ValueError('Public resolver returned an inconclusive answer')
    return data


def txt_records(data):
    # Google's presentation form may contain adjacent quoted TXT chunks.
    out=[]
    for answer in data.get('Answer',[]):
        if answer.get('type')==16:
            raw=str(answer.get('data',''))
            chunks=re.findall(r'"((?:[^"\\]|\\.)*)"',raw)
            out.append(''.join(chunks) if chunks else raw)
    return out


def policy(kind,data):
    records=txt_records(data)
    if kind=='spf':
        found=[r for r in records if re.match(r'^v=spf1(?:\s|$)',r,re.I)]
        return len(found)==1,'SPF record present' if len(found)==1 else 'SPF missing or multiple records'
    if kind=='dmarc':
        found=[r for r in records if re.match(r'^v=DMARC1\s*(?:;|$)',r,re.I)]
        valid=len(found)==1 and bool(re.search(r'(?:^|;)\s*p\s*=\s*(none|quarantine|reject)\s*(?:;|$)',found[0],re.I))
        return valid,'DMARC policy present' if valid else 'DMARC missing or invalid policy'
    found=[r for r in records if re.search(r'(?:^|;)\s*p\s*=\s*[A-Za-z0-9+/=]+\s*(?:;|$)',r)]
    return len(found)==1,'DKIM public key present' if len(found)==1 else 'DKIM key missing or ambiguous'


async def email_health(host,config,get_json):
    selector=config.get('selector','')
    queries=[('mx',host,'MX'),('spf',host,'TXT'),('dmarc','_dmarc.'+host,'TXT')]
    if selector:
        queries.append(('dkim',selector+'._domainkey.'+host,'TXT'))
    outcomes=await asyncio.gather(*(dns_query(name,typ,get_json) for _,name,typ in queries),return_exceptions=True)
    labels=[];failed=[];uncertain=False
    for (kind,_,_),data in zip(queries,outcomes):
        if isinstance(data,Exception):
            labels.append(kind.upper()+': unknown');uncertain=True;continue
        if kind=='mx':
            valid=any(a.get('type')==15 and str(a.get('data','')).split()[-1:] not in ([],['.']) for a in data.get('Answer',[]))
            label='MX routing present' if valid else 'MX missing or null MX'
        else:
            valid,label=policy(kind,data)
        labels.append(label)
        if not valid:failed.append(kind.upper())
    if not selector:labels.append('DKIM not configured')
    detail='; '.join(labels)[:250]
    if failed:return result(error='Email DNS issues: '+', '.join(failed),detail=detail)
    if uncertain:return result(error='Email DNS lookup incomplete',detail=detail,unknown=True)
    return result(detail=detail)


async def blacklist(host,config,get_json):
    zone=DNSBL_ZONES[config['list']]
    if config['list']=='spamhaus_zen':
        ip=ipaddress.ip_address(await resolve_public(host,25))
        if ip.version!=4 or not public_address(str(ip)):
            return result(error='Selected DNSBL requires public IPv4',unknown=True)
        name='.'.join(reversed(str(ip).split('.')))+'.'+zone
    else:
        name=host+'.'+zone
    # Public recursive resolvers can be blocked by DNSBL providers. Only
    # documented positive responses count; ambiguous empty answers are unknown.
    data=await dns_query(name,'A',get_json)
    answers=[str(a.get('data','')) for a in data.get('Answer',[]) if a.get('type')==1]
    if any(a.startswith('127.255.255.') for a in answers):
        return result(error='DNSBL provider refused this resolver',unknown=True)
    if config['list']=='spamhaus_zen':
        listed=any(a in {'127.0.0.2','127.0.0.3','127.0.0.4','127.0.0.9','127.0.0.10','127.0.0.11'} for a in answers)
    else:
        listed=any(a in {'127.0.1.2','127.0.1.4','127.0.1.5','127.0.1.6'} for a in answers)
    if listed:return result(error='Listed by selected DNSBL',detail=zone)
    return result(error='DNSBL negative result cannot be verified via public resolver',detail=zone,unknown=True)


async def check(kind,host,config,get_json):
    try:
        if kind in PROTOCOL_PORTS:
            return await protocol(kind,host,config['port'])
        if kind=='email_health':
            return await email_health(host,config,get_json)
        if kind=='blacklist':
            return await blacklist(host,config,get_json)
        if kind=='dnssec':
            data=await dns_query(host,'A',get_json,do='true',cd='false')
            if data.get('AD') is True and any(a.get('type')==1 for a in data.get('Answer',[])):
                return result(detail='Public resolver authenticated the A answer (AD flag)')
            return result(error='Authenticated A answer not returned',unknown=True)
        name=host if kind=='spf' else '_dmarc.'+host if kind=='dmarc' else config['selector']+'._domainkey.'+host
        valid,detail=policy(kind,await dns_query(name,'TXT',get_json))
        return result(error=None if valid else detail,detail=detail)
    except (OSError,ValueError,TimeoutError,KeyError,TypeError) as exc:
        return result(error=kind.upper()+' check failed: '+type(exc).__name__,unknown=kind in DNS_KINDS)
