"""Durable outbound incident notifications. Only fixed provider HTTPS endpoints are accepted."""
import asyncio
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit
import os

import h11

def endpoint(kind, value):
    value = value.strip()
    u = urlsplit(value)
    if kind == 'telegram':
        if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}\\|(?:-?[0-9]{1,20}|@[A-Za-z0-9_]{5,32})',value):
            raise ValueError('Enter bot token, a vertical bar, and chat ID')
        return value
    if u.scheme != 'https' or u.username or u.password or u.port not in (None,443) or u.fragment or any(ord(ch)<33 or ord(ch)>126 for ch in value):
        raise ValueError('Use the provider HTTPS webhook URL without query parameters')
    if kind == 'webhook':
        if not u.hostname or not u.path or u.path == '/':
            raise ValueError('Enter a public HTTPS URL with a specific endpoint path')
        return value
    if kind == 'teams':
        host=u.hostname or ''
        if not (host.endswith('.logic.azure.com') or host.endswith('.api.powerplatform.com')):
            raise ValueError('Use a Teams Workflows HTTPS URL')
        if not re.fullmatch(r'/.*?/workflows/[A-Za-z0-9-]+/triggers/[^/]+/paths/invoke/?',u.path) and not re.fullmatch(r'/workflows/[A-Za-z0-9-]+/triggers/[^/]+/paths/invoke/?',u.path):
            raise ValueError('Invalid Teams workflow path')
        if not u.query or 'sig=' not in u.query:
            raise ValueError('Use the complete signed workflow URL')
        return value
    if u.query:
        raise ValueError('Provider webhook cannot contain query parameters')
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
    columns={r['name'] for r in c.execute('PRAGMA table_info(alert_channels)')}
    if 'monitor_id' not in columns:
        c.execute('ALTER TABLE alert_channels ADD COLUMN monitor_id INTEGER REFERENCES monitors(id) ON DELETE CASCADE')
    if 'delay_minutes' not in columns:
        c.execute('ALTER TABLE alert_channels ADD COLUMN delay_minutes INTEGER NOT NULL DEFAULT 0')
    columns={r['name'] for r in c.execute('PRAGMA table_info(incidents)')}
    if 'acknowledged_at' not in columns:
        c.execute('ALTER TABLE incidents ADD COLUMN acknowledged_at TEXT')
    c.execute("""CREATE TABLE IF NOT EXISTS escalations(
        incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        channel_id INTEGER NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
        PRIMARY KEY(incident_id,channel_id)
    )""")


def enqueue(c, user_id, subject, body, monitor_id=None, incident_id=None, recovery=False):
    clock = int(time.time())
    c.execute("""INSERT INTO channel_outbox(channel_id,subject,body,created_at,next_attempt)
        SELECT id,?,?,?,? FROM alert_channels WHERE user_id=?
        AND (monitor_id IS NULL OR monitor_id=?)
        AND (delay_minutes=0 OR (?=1 AND EXISTS(
            SELECT 1 FROM escalations e WHERE e.channel_id=alert_channels.id AND e.incident_id=?
        )))""",(subject,body,clock,clock,user_id,monitor_id,int(recovery),incident_id))


def enqueue_escalations(c, site_url, now_iso):
    clock=int(time.time())
    rows=c.execute("""SELECT i.id incident_id,m.id monitor_id,m.name,a.id channel_id,i.opened_at
        FROM incidents i JOIN monitors m ON m.id=i.monitor_id
        JOIN users u ON u.id=m.user_id JOIN alert_channels a ON a.user_id=u.id
        WHERE i.resolved_at IS NULL AND i.acknowledged_at IS NULL
        AND u.email_verified_at IS NOT NULL AND u.email_alerts_enabled=1
        AND m.enabled=1 AND m.state='down' AND a.delay_minutes>0
        AND (a.monitor_id IS NULL OR a.monitor_id=m.id)
        AND unixepoch(i.opened_at)+a.delay_minutes*60<=?
        AND NOT EXISTS(SELECT 1 FROM escalations e WHERE e.incident_id=i.id AND e.channel_id=a.id)
        LIMIT 100""",(clock,)).fetchall()
    for row in rows:
        # Mark once, in the same transaction as the queue insert.
        if c.execute('INSERT OR IGNORE INTO escalations VALUES(?,?)',(row['incident_id'],row['channel_id'])).rowcount:
            c.execute('INSERT INTO channel_outbox(channel_id,subject,body,created_at,next_attempt) VALUES(?,?,?,?,?)',
                (row['channel_id'],'BeepNado escalation: '+row['name'],
                 'Unacknowledged outage opened '+row['opened_at']+'. Investigate at '+site_url,clock,clock))

def payload(kind, subject, body):
    text = (subject + '\n' + body)[:1800]
    if kind == 'discord':
        return {'content':text,'allowed_mentions':{'parse':[]}}
    if kind == 'teams':
        return {'type':'message','attachments':[{'contentType':'application/vnd.microsoft.card.adaptive',
            'content':{'type':'AdaptiveCard','version':'1.2','body':[{'type':'TextBlock','text':text,'wrap':True}]}}]}
    if kind == 'webhook':
        return {'source':'beepnado','subject':subject,'body':body}
    # Plain text blocks prevent customer names from becoming Slack mentions.
    return {'text':'BeepNado incident update','blocks':[{'type':'section','text':{'type':'plain_text','text':text}}]}

async def deliver(row, validate_url):
    import ssl
    url = endpoint(row['kind'],row['endpoint'])
    message=payload(row['kind'],row['subject'],row['body'])
    if row['kind']=='telegram':
        token,chat=url.split('|',1)
        url='https://api.telegram.org/bot'+token+'/sendMessage'
        message={'chat_id':chat,'text':(row['subject']+'\\n'+row['body'])[:3500]}
    host, ip, port, path, scheme = await validate_url(url)
    data = json.dumps(message).encode()
    if row['kind']=='webhook' and os.getenv('ENABLE_GENERIC_WEBHOOKS','false').lower()!='true':
        raise ValueError('Generic webhook delivery disabled')
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
