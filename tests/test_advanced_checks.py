import asyncio
import unittest
from unittest.mock import patch
from app import advanced_checks as a, main
from app.network_checks import NetworkInput


class AdvancedChecksTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_greeting(self):
        async def server(reader,writer):
            writer.write(b'220 Test SMTP\r\n')
            await writer.drain()
            writer.close()
        listener=await asyncio.start_server(server,'127.0.0.1',0)
        real_open=asyncio.open_connection
        async def connect(ip,port,**kwargs):
            return await real_open('127.0.0.1',listener.sockets[0].getsockname()[1],**kwargs)
        async def resolve(host,port):
            return '93.184.216.34'
        async with listener:
            with patch.object(a,'resolve_public',resolve),patch.object(a.asyncio,'open_connection',connect):
                answer=await a.protocol('smtp','example.net',25)
        self.assertEqual(answer['state'],'up')
        self.assertIsNotNone(answer['response_ms'])

    async def test_mail_policy(self):
        from urllib.parse import parse_qs,urlsplit
        async def lookup(url):
            q=parse_qs(urlsplit(url).query)
            name=q['name'][0]
            typ=q['type'][0]
            if typ=='MX':
                return {'Status':0,'Answer':[{'type':15,'data':'10 mail.example.net.'}]}
            value='v=spf1 -all'
            if name.startswith('_dmarc.'):
                value='v=DMARC1; p=reject'
            if '._domainkey.' in name:
                value='v=DKIM1; p=YWJj'
            return {'Status':0,'Answer':[{'type':16,'data':'"'+value+'"'}]}
        result=await a.email_health('example.net',{'selector':'selector1'},lookup)
        self.assertEqual(result['state'],'up')
        self.assertIn('DKIM public key present',result['detail'])
        self.assertFalse(a.policy('dmarc',{'Answer':[{'type':16,'data':'"v=DMARC1; p=bad"'}]})[0])

    async def test_dnssec_and_blacklist_uncertainty(self):
        async def lookup(url):
            return {'Status':0,'Answer':[{'type':1,'data':'127.255.255.254'}]}
        result=await a.blacklist('example.net',{'list':'spamhaus_dbl'},lookup)
        self.assertEqual(result['state'],'unknown')
        result=await a.check('dnssec','example.net',{},lookup)
        self.assertEqual(result['state'],'unknown')

    async def test_target_validation(self):
        with self.assertRaises(ValueError):
            await main.network_target(NetworkInput(name='SMTP',kind='smtp',target='example.net',port=80,authorized=True))
        with self.assertRaises(ValueError):
            await main.network_target(NetworkInput(name='DKIM',kind='dkim',target='example.net',authorized=True))
        with patch.dict('os.environ',{'ENABLE_DNSBL':'false'}):
            with self.assertRaises(ValueError):
                await main.network_target(NetworkInput(name='DNSBL',kind='blacklist',target='example.net',authorized=True))


if __name__=='__main__':
    unittest.main()
