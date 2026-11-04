"""Public network probes. All sockets connect to validated, pinned addresses."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import re
import secrets
import socket
import ssl
import struct
import time
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field

DNS_TYPES = {'A': 1, 'AAAA': 28, 'CNAME': 5, 'MX': 15, 'NS': 2, 'TXT': 16, 'CAA': 257}
EXPIRY_INTERVALS = {'ssl': 3600, 'domain': 21600}
TRANSITION_NETWORKS = tuple(map(ipaddress.ip_network, ('64:ff9b::/96', '64:ff9b:1::/48', '2001::/32', '2002::/16')))
rdap_bootstrap = None
rdap_lock = asyncio.Lock()


class NetworkInput(BaseModel):
    name: str = Field(min_length=2, max_length=100, pattern=r'^[^\r\n]+$')
    kind: Literal['api', 'dns', 'ssl', 'domain', 'ping', 'tcp', 'udp']
    target: str = Field(min_length=3, max_length=1500)
    authorized: bool
    port: int | None = Field(default=None, ge=1, le=65535)
    days: int = Field(default=14, ge=1, le=365)
    record_type: Literal['A', 'AAAA', 'CNAME', 'MX', 'NS', 'TXT', 'CAA'] = 'A'
    expected: str = Field(default='', max_length=512)
    payload: str = Field(default='', max_length=384)
    response_prefix: str = Field(default='', max_length=384)
    status: int = Field(default=200, ge=200, le=299)
    json_path: str = Field(default='', max_length=200)


def public_hostname(raw, *, dns_record=False):
    host = raw.strip().lower().rstrip('.')
    label = r'[a-z0-9_-]+' if dns_record else r'[a-z0-9-]+'
    if (len(host) > 253 or not re.fullmatch(rf'{label}(?:\.{label})+', host)
            or any(len(part) > 63 or part.startswith('-') or part.endswith('-') for part in host.split('.'))):
        raise ValueError('Use a valid public domain name')
    if host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid', '.example', '.onion')):
        raise ValueError('Use a real public domain')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError('Use a public domain name without an IP literal')


def public_address(value):
    addr = ipaddress.ip_address(value)
    if not addr.is_global or addr.is_multicast or addr.is_reserved:
        return False
    if addr.version == 6:
        return (addr.ipv4_mapped is None and not addr.is_site_local and not addr.scope_id
                and not any(addr in network for network in TRANSITION_NETWORKS))
    return True


async def resolve_public(host, port):
    entries = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 5,
    )
    ips = {entry[4][0] for entry in entries}
    if not ips or not all(public_address(ip) for ip in ips):
        raise ValueError('The domain resolves to a private or reserved address')
    return min(ips, key=lambda ip: (':' in ip, ip))


def check_hex(value, *, required):
    try:
        data = bytes.fromhex(value)
    except ValueError:
        raise ValueError('Use pairs of hex digits for UDP data') from None
    if len(data) > 128 or (required and not data):
        raise ValueError('UDP requests need 1 to 128 bytes. Reply prefixes can be up to 128 bytes.')
    return data.hex()


def result(*, error=None, detail=None, response_ms=None, cert_days=None, unknown=False):
    return {
        'state': 'unknown' if unknown else 'down' if error else 'up',
        'http_code': None, 'response_ms': response_ms, 'cert_days': cert_days, 'error': error,
        'detail': detail,
    }


async def ping_address(ip):
    """Linux ICMP datagram socket with kernel-managed identifiers and checksums."""
    v6 = ':' in ip
    family = socket.AF_INET6 if v6 else socket.AF_INET
    protocol = socket.IPPROTO_ICMPV6 if v6 else socket.IPPROTO_ICMP
    loop = asyncio.get_running_loop()
    sequence = secrets.randbelow(65536)
    nonce = secrets.token_bytes(16)
    packet = struct.pack('!BBHHH', 128 if v6 else 8, 0, 0, 0, sequence) + nonce
    with socket.socket(family, socket.SOCK_DGRAM, protocol) as connection:
        connection.setblocking(False)
        async with asyncio.timeout(5):
            await loop.sock_connect(connection, (ip, 0))
            await loop.sock_sendall(connection, packet)
            while True:
                reply = await loop.sock_recv(connection, 256)
                if len(reply) >= 8 and reply[0] == (129 if v6 else 0) and reply[1] == 0:
                    if struct.unpack_from('!H', reply, 6)[0] == sequence and reply[8:] == nonce:
                        return


async def udp_reply(ip, port, payload):
    loop = asyncio.get_running_loop()
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as connection:
        connection.setblocking(False)
        async with asyncio.timeout(6):
            await loop.sock_connect(connection, (ip, port))
            await loop.sock_sendall(connection, payload)
            return await loop.sock_recv(connection, 512)


def expiry_result(label, expires, threshold, *, certificate=False):
    seconds = (expires - datetime.now(timezone.utc)).total_seconds()
    days = int(seconds // 86400)
    detail = f'{label} expires {expires.date().isoformat()} UTC ({days} days left)'
    error = f'{label} expires in less than {threshold} days' if seconds < threshold * 86400 else None
    return result(error=error, detail=detail, cert_days=days if certificate else None)


def normalize_dns(record, value):
    value = value.strip()
    if record in ('A', 'AAAA'):
        address = ipaddress.ip_address(value)
        if address.version != (4 if record == 'A' else 6):
            raise ValueError(f'{record} expects an IPv{4 if record == "A" else 6} address')
        return str(address)
    if record in ('CNAME', 'MX', 'NS'):
        return value.lower().rstrip('.')
    return value.strip('"') if record == 'TXT' else value


async def dns_result(host, config, get_json):
    record = config['record_type']
    payload = await get_json('https://dns.google/resolve?' + urlencode({'name': host, 'type': record}))
    status = payload.get('Status')
    if status == 3:
        return result(error='DNS name does not exist (NXDOMAIN)', detail=f'{record} lookup returned NXDOMAIN')
    if status != 0 or payload.get('TC'):
        return result(error='Public DNS lookup was inconclusive', unknown=True)
    data = [str(row['data']) for row in payload.get('Answer', [])
            if isinstance(row, dict) and row.get('type') == DNS_TYPES[record] and 'data' in row]
    if not data:
        return result(error=f'No {record} answer returned', detail=f'{record} answer was empty')
    detail = f'{record}: ' + ', '.join(data)[:160]
    expected = config['expected']
    error = f'Expected {record} answer was missing' if expected and expected not in {
        normalize_dns(record, value) for value in data
    } else None
    return result(error=error, detail=detail)


async def registration_result(host, config, get_json):
    global rdap_bootstrap
    async with rdap_lock:
        if rdap_bootstrap is None or time.monotonic() >= rdap_bootstrap[0]:
            directory = await get_json('https://data.iana.org/rdap/dns.json')
            rdap_bootstrap = (time.monotonic() + 86400, directory['services'])
        services = rdap_bootstrap[1]
    matches = [(suffix.count('.'), base) for suffixes, bases in services
               for suffix in suffixes if suffix and (host == suffix or host.endswith('.' + suffix))
               for base in bases if base.startswith('https://')]
    if not matches:
        return result(error='No HTTPS RDAP service is published for this domain', unknown=True)
    depth = max(n for n, _ in matches)
    # Bootstrap entries may list alternatives for the same suffix.
    for base in dict.fromkeys(base for n, base in matches if n == depth):
        try:
            data = await get_json(base.rstrip('/') + '/domain/' + host)
            if str(data.get('ldhName', '')).rstrip('.').lower() != host:
                continue
            dates = [datetime.fromisoformat(event['eventDate'].replace('Z', '+00:00'))
                     for event in data.get('events', []) if event.get('eventAction') == 'expiration']
            dates = [date.astimezone(timezone.utc) for date in dates if date.tzinfo is not None]
            if dates:
                return expiry_result('Domain registration', min(dates), config['days'])
        except (ValueError, TypeError, KeyError, OSError, TimeoutError):
            continue
    return result(error='The registry did not provide a usable registration expiry date', unknown=True)


async def probe_network(kind, host, config, *, get_json, route_tls):
    started = time.monotonic()
    try:
        if kind == 'dns':
            return await dns_result(host, config, get_json)
        if kind == 'domain':
            return await registration_result(host, config, get_json)
        port = config.get('port', 443)
        ip = await resolve_public(host, port)
        if kind == 'ping':
            await ping_address(ip)
            detail = 'ICMP echo reply received'
        elif kind == 'udp':
            reply = await udp_reply(ip, port, bytes.fromhex(config['payload']))
            prefix = bytes.fromhex(config['response_prefix'])
            if not reply.startswith(prefix):
                return result(error='UDP reply did not match the expected prefix',
                              detail=f'Received a UDP reply on port {port}')
            detail = f'UDP reply received on port {port}'
        else:
            tls = kind == 'ssl'
            context = ssl.create_default_context() if tls else None
            async with asyncio.timeout(8):
                _, writer = await asyncio.open_connection(
                    route_tls(host, ip) if tls else ip, port, ssl=context, server_hostname=host if tls else None,
                )
            try:
                if tls:
                    cert = writer.get_extra_info('ssl_object').getpeercert()
                    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert['notAfter']), timezone.utc)
                    return expiry_result('TLS certificate', expires, config['days'], certificate=True)
                detail = f'TCP connection succeeded on port {port}'
            finally:
                writer.close()
        return result(detail=detail, response_ms=int((time.monotonic() - started) * 1000))
    except PermissionError:
        if kind == 'ping':
            return result(error='ICMP is unavailable from this worker', unknown=True)
        return result(error='Network access was denied', unknown=True)
    except ssl.SSLCertVerificationError as exc:
        return result(error=f'TLS certificate verification failed: {exc.verify_message}', detail='TLS handshake reached the server')
    except TimeoutError:
        return result(error=f'{kind.upper()} check timed out', unknown=kind in ('domain', 'dns'))
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        return result(error=f'{kind.upper()} check failed: {str(exc)[:95]}', unknown=kind in ('domain', 'dns'))
