import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main


class PerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_timing_and_body_limit(self):
        async def respond(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(respond,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        async def target(url):
            return 'example.net','127.0.0.1',port,'/','http'
        async with server:
            with patch.object(main,'validate_url',target):
                result=await main.probe('http://example.net/',public_only=True)
        self.assertEqual(result['state'],'up')
        timings=result['timings']
        for key in ('dns_ms','tcp_ms','ttfb_ms','headers_ms','body_ms','total_ms'):
            self.assertGreaterEqual(timings[key],0)
        self.assertNotIn('tls_ms',timings)
        self.assertEqual(timings['body_bytes'],5)
        self.assertGreaterEqual(timings['total_ms'],timings['headers_ms'])

    async def test_timing_persistence_and_incident_evidence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                uid=c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',
                              ('timing@example.net','unused',main.now())).lastrowid
                mid=c.execute('INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)',
                              (uid,'Example','https://example.net',main.now())).lastrowid
            result=dict(main.network_result(error='HTTP 503'),http_code=503,
                        timings={'dns_ms':12,'tcp_ms':20,'ttfb_ms':50,'route':'public'})
            main.record_check(mid,result)
            main.record_check(mid,result)
            with main.db() as c:
                check=c.execute('SELECT timings FROM checks WHERE monitor_id=? ORDER BY id DESC',(mid,)).fetchone()
                evidence=c.execute('SELECT opening_evidence FROM incidents WHERE monitor_id=?',(mid,)).fetchone()
                self.assertEqual(json.loads(check['timings']),result['timings'])
                self.assertEqual(json.loads(evidence['opening_evidence'])['timings'],result['timings'])


if __name__=='__main__':
    unittest.main()
