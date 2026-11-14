"""Durable outbound incident notifications. Only fixed provider HTTPS endpoints are accepted."""
import asyncio
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit

import h11

def endpoint(kind, value):
    value = value.strip()
    u = urlsplit(value)
    if u.scheme != 'https' or u.username or u.password or u.port not in (None,443) or u.query or u.fragment:
        raise ValueError('Use the provider HTTPS webhook URL without query parameters')
    patterns = {
        'slack': ('hooks.slack.com', r'/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+'),
        'discord': ('discord.com', r'/api/webhooks/[0-9]+/[A-Za-z0-9_-]+'),
    }
    if kind not in patterns or u.hostname != patterns[kind][0] or not re.fullmatch(patterns[kind][1], u.path):
        raise ValueError('Invalid provider webhook URL')
    return value

def init(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS alert_channels(
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, label TEXT NOT NULL, endpoint TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS channel_outbox(
            id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
            subject TEXT NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL,
            next_attempt INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, sent_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS channel_delivery_due ON channel_outbox(sent_at,next_attempt);
    """)

def enqueue(c, user_id, subject, body):
    clock = int(time.time())
    c.execute("""INSERT INTO channel_outbox(channel_id,subject,body,created_at,next_attempt)
        SELECT id,?,?,?,? FROM alert_channels WHERE user_id=?""",
        (subject,body,clock,clock,user_id))

def payload(kind, subject, body):
    text = (subject + '\n' + body)[:1800]
    if kind == 'discord':
        return {'content':text,'allowed_mentions':{'parse':[]}}
    # Plain text blocks prevent customer names from becoming Slack mentions.
    return {'text':'BeepNado incident update','blocks':[{'type':'section','text':{'type':'plain_text','text':text}}]}

async def deliver(row, validate_url):
    import ssl
    url = endpoint(row['kind'],row['endpoint'])
    host, ip, port, path, scheme = await validate_url(url)
    data = json.dumps(payload(row['kind'],row['subject'],row['body'])).encode()
    async with asyncio.timeout(12):
        reader,writer = await asyncio.open_connection(ip,port,ssl=ssl.create_default_context(),server_hostname=host)
        try:
            conn = h11.Connection(h11.CLIENT)
            writer.write(conn.send(h11.Request(method='POST',target=path,headers=[
                ('Host',host),('Content-Type','application/json'),('Content-Length',str(len(data))),('Connection','close')
            ])) + conn.send(h11.Data(data=data)) + conn.send(h11.EndOfMessage()))
            await writer.drain()
            while True:
                event = conn.next_event()
                if event is h11.NEED_DATA:
                    chunk = await reader.read(8192)
                    if not chunk:
                        raise ValueError('Incomplete webhook response')
                    conn.receive_data(chunk)
                elif isinstance(event,h11.Response):
                    if 200 <= event.status_code < 300:
                        return
                    raise ValueError('Provider rejected webhook')
                elif not isinstance(event,h11.InformationalResponse):
                    raise ValueError('Invalid webhook response')
        finally:
            writer.close()

async def drain(db, validate_url):
    clock = int(time.time())
    with db() as c:
        c.execute('DELETE FROM channel_outbox WHERE created_at<?',(clock-7*86400,))
        rows = [dict(r) for r in c.execute("""SELECT o.*,a.kind,a.endpoint FROM channel_outbox o
            JOIN alert_channels a ON a.id=o.channel_id WHERE o.sent_at IS NULL AND o.attempts<7
            AND o.next_attempt<=? AND o.id IN (SELECT MIN(id) FROM channel_outbox WHERE sent_at IS NULL AND attempts<7 GROUP BY channel_id)
            ORDER BY o.id LIMIT 12""",(clock,))]
    for row in rows:
        try:
            await deliver(row,validate_url)
        except Exception as exc:
            print('Integration delivery deferred:',type(exc).__name__,flush=True)
            with db() as c:
                c.execute('UPDATE channel_outbox SET attempts=attempts+1,next_attempt=? WHERE id=?',
                          (int(time.time())+min(3600,60*2**row['attempts']),row['id']))
        else:
            with db() as c:
                c.execute('UPDATE channel_outbox SET sent_at=? WHERE id=?',(int(time.time()),row['id']))
