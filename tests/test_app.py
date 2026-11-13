import asyncio
import os
import tempfile
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

_tmp=tempfile.TemporaryDirectory()
os.environ['DATABASE_PATH']=str(Path(_tmp.name)/'test.sqlite3')
os.environ['SESSION_SECURE']='false'
os.environ['STRIPE_SECRET_KEY']=''
os.environ['STRIPE_WEBHOOK_SECRET']=''
os.environ['REGISTRATION_ENABLED']='true'
os.environ['SMTP_HOST']='test.smtp.example'
os.environ['SMTP_FROM']='alerts@test.example'
os.environ['SMTP_STARTTLS']='true'
from app import main


class BeepNadoTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(main.app)
        self.client.__enter__()
        self.email=f'user-{os.urandom(6).hex()}@example.net'
        with patch.object(main,'send_email') as mock_mail:
            r=self.client.post('/api/register',json={'email':self.email,'password':'LongRandomPassword123!'},headers={'Origin':'http://testserver'})
        self.assertEqual(r.status_code,202,r.text)
        token=re.search(r'#verify=([^\s]+)',mock_mail.call_args.args[2]).group(1)
        verified=self.client.post('/api/verify-email',json={'token':token},headers={'Origin':'http://testserver'})
        self.assertEqual(verified.status_code,200,verified.text)
        self.csrf=self.client.get('/api/me').json()['user']['csrf']
        self.headers={'Origin':'http://testserver','X-CSRF-Token':self.csrf}

    def tearDown(self):
        self.client.__exit__(None,None,None)

    def test_persistent_email_alarm(self):
        settings = self.client.get('/api/alarm-settings')
        self.assertEqual(settings.status_code, 200)
        self.assertTrue(settings.json()['armed'])
        self.assertEqual(settings.json()['email'], self.email)
        self.assertEqual(self.client.put('/api/alarm-settings', json={'armed':False}).status_code,403)
        self.assertEqual(self.client.put('/api/alarm-settings', headers=self.headers,
                                         json={'armed':False}).status_code,200)
        uid = self.client.get('/api/me').json()['user']['id']
        with main.db() as c:
            main.enqueue_alert(c, uid, 'Disarmed alert', 'Should not be queued')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM email_outbox WHERE recipient=? AND subject=?',
                                       (self.email,'Disarmed alert')).fetchone()[0],0)
        self.assertEqual(self.client.post('/api/alarm-settings/test',headers=self.headers).status_code,409)
        self.assertFalse(self.client.get('/api/alarm-settings').json()['armed'])
        self.assertTrue(self.client.put('/api/alarm-settings', headers=self.headers,
                                        json={'armed':True}).json()['armed'])
        self.assertTrue(self.client.post('/api/alarm-settings/test',headers=self.headers).json()['queued'])
        self.assertEqual(self.client.post('/api/logout',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/alarm-settings').status_code,401)
        main.init_db()
        with main.db() as c:
            main.enqueue_alert(c, uid, 'Offline outage', 'Browser is closed')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM email_outbox WHERE recipient=? AND subject=?',
                                       (self.email,'Offline outage')).fetchone()[0],1)
        self.assertEqual(self.client.post('/api/login',json={'email':self.email,
                         'password':'LongRandomPassword123!'},headers={'Origin':'http://testserver'}).status_code,200)
        self.assertTrue(self.client.get('/api/alarm-settings').json()['armed'])

    def test_register_sessions_and_account_guard(self):
        self.assertEqual(self.client.get('/api/dashboard').status_code,200)
        self.assertEqual(self.client.post('/api/status-page',json={'title':'My Status','slug':'my-test-status'}).status_code,403)
        self.assertEqual(self.client.get('/internal/targets').status_code,401)
        self.assertEqual(self.client.post('/api/billing/checkout',json={'plan':'pro'},headers=self.headers).status_code,422)
        result=self.client.post('/api/logout',headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.client.get('/api/dashboard').status_code,401)

    def test_status_page_public_and_tenant_isolation(self):
        slug='status-'+os.urandom(5).hex()
        self.assertEqual(self.client.post('/api/status-page',headers=self.headers,json={'title':'Availability','slug':slug}).status_code,200)
        res=self.client.get('/api/public-status/'+slug)
        self.assertEqual(res.status_code,200)
        self.assertEqual(res.json()['monitors'],[])
        self.assertEqual(self.client.get('/s/'+slug).status_code,200)
        self.assertEqual(self.client.get('/s/not-a-real-page').status_code,404)
        with TestClient(main.app) as outsider:
            self.assertEqual(outsider.get('/api/dashboard').status_code,401)

    def test_owner_only_same_host_route_never_bypasses_public_dns_guard(self):
        with patch.object(main, 'SELF_HOSTED_PUBLIC_IP', '68.205.135.160'), \
             patch.object(main, 'SELF_HOSTED_DOMAINS', frozenset({'rebootsyndicate.com'})):
            self.assertEqual(main.probe_connection_target('rebootsyndicate.com', '68.205.135.160'), 'jason-portfolio')
            self.assertEqual(main.probe_connection_target('other-site.com', '68.205.135.160'), '68.205.135.160')
            self.assertEqual(main.probe_connection_target('rebootsyndicate.com.attacker.net', '68.205.135.160'), '68.205.135.160')
            self.assertEqual(main.probe_connection_target('rebootsyndicate.com', '8.8.8.8'), '8.8.8.8')
            self.assertEqual(main.probe_connection_target('rebootsyndicate.com', '192.168.1.130'), '192.168.1.130')
            self.assertEqual(main.probe_connection_target('rebootsyndicate.com', '127.0.0.1'), '127.0.0.1')

    def test_self_host_override_does_not_disable_public_dns_validation(self):
        async def private_dns(*args,**kwargs):
            return [(None,None,None,None,('192.168.1.130',443))]
        with patch.object(main, 'SELF_HOSTED_PUBLIC_IP', '68.205.135.160'), \
             patch.object(main, 'SELF_HOSTED_DOMAINS', frozenset({'rebootsyndicate.com'})), \
             patch('asyncio.base_events.BaseEventLoop.getaddrinfo', new=private_dns):
            r=self.client.post('/api/monitors',headers=self.headers,json={'name':'Internal alias','url':'https://rebootsyndicate.com','authorized':True})
            self.assertEqual(r.status_code,422,r.text)

    def test_url_validation_rejects_private_and_third_party_redirects(self):
        async def private_dns(*args,**kwargs):
            return [(None,None,None,None,('127.0.0.1',443))]
        with patch('asyncio.base_events.BaseEventLoop.getaddrinfo', new=private_dns):
            r=self.client.post('/api/monitors',headers=self.headers,json={'name':'Internal API','url':'https://internal.example.org','authorized':True})
            self.assertEqual(r.status_code,422,r.text)
        r=self.client.post('/api/monitors',headers=self.headers,json={'name':'Root','url':'http://127.0.0.1','authorized':True})
        self.assertEqual(r.status_code,422)
        r=self.client.post('/api/monitors',headers=self.headers,json={'name':'Credential leak','url':'https://user:pass@example.com','authorized':True})
        self.assertEqual(r.status_code,422)
        r=self.client.post('/api/monitors',headers=self.headers,json={'name':'No auth','url':'https://example.com','authorized':False})
        self.assertEqual(r.status_code,422)

    def test_monitor_limit_incident_recovery_and_isolation(self):
        with patch.object(main,'validate_url',new=AsyncMock(return_value=('example.com','93.184.216.34',443,'/','https'))):
            a=self.client.post('/api/monitors',headers=self.headers,json={'name':'Website A','url':'https://example.com','authorized':True})
            self.assertEqual(a.status_code,201,a.text)
            mid=a.json()['id']
            b=self.client.post('/api/monitors',headers=self.headers,json={'name':'Website B','url':'https://example.com','authorized':True})
            self.assertEqual(b.status_code,201,b.text)
            limit=self.client.post('/api/monitors',headers=self.headers,json={'name':'Website C','url':'https://example.com','authorized':True})
            self.assertEqual(limit.status_code,403)
        failed=dict(state='down',http_code=500,response_ms=100,cert_days=30,error='HTTP 500')
        main.record_check(mid,failed)
        self.assertEqual(self.client.get('/api/dashboard').json()['monitors'][1]['state'],'degraded')
        main.record_check(mid,failed)
        incident=self.client.get('/api/dashboard').json()['incidents'][0]
        self.assertIsNone(incident['resolved_at'])
        self.assertEqual(self.client.get('/api/dashboard').json()['monitors'][1]['state'],'down')
        main.record_check(mid,dict(state='up',http_code=200,response_ms=90,cert_days=30,error=None))
        self.assertIsNotNone(self.client.get('/api/dashboard').json()['incidents'][0]['resolved_at'])
        with TestClient(main.app) as stranger:
            with patch.object(main,'send_email') as mail:
                r=stranger.post('/api/register',json={'email':f'other-{os.urandom(5).hex()}@example.org','password':'OtherRandomPassword123!'},headers={'Origin':'http://testserver'})
            self.assertEqual(r.status_code,202)
            stranger.post('/api/verify-email',json={'token':re.search(r'#verify=([^\s]+)',mail.call_args.args[2]).group(1)},headers={'Origin':'http://testserver'})
            other_csrf=stranger.get('/api/me').json()['user']['csrf']
            other_header={'Origin':'http://testserver','X-CSRF-Token':other_csrf}
            self.assertEqual(stranger.get('/api/dashboard').json()['monitors'],[])
            self.assertEqual(stranger.delete(f'/api/monitors/{mid}',headers=other_header).status_code,404)
            self.assertEqual(stranger.post(f'/api/monitors/{mid}/check',headers=other_header).status_code,404)
        self.assertEqual(self.client.delete(f'/api/monitors/{mid}',headers=self.headers).status_code,200)

    def test_outage_and_recovery_emit_tenant_scoped_alerts(self):
        with patch.object(main,'validate_url',new=AsyncMock(return_value=('example.com','93.184.216.34',443,'/','https'))):
            result=self.client.post('/api/monitors',headers=self.headers,json={
                'name':'Checkout Website','url':'https://example.com','authorized':True})
        self.assertEqual(result.status_code,201)
        mid=result.json()['id']
        self.assertEqual(self.client.get('/api/dashboard').json()['notifications'],[])
        failure=dict(state='down',http_code=503,response_ms=450,cert_days=None,error='HTTP 503')
        main.record_check(mid,failure)
        self.assertEqual(self.client.get('/api/dashboard').json()['notifications'],[])
        self.assertEqual(self.client.get('/api/alarm-incidents').json()['incidents'], [])
        main.record_check(mid,failure)
        notices=self.client.get('/api/dashboard').json()['notifications']
        self.assertEqual(len(notices),1)
        self.assertEqual(notices[0]['kind'],'outage')
        alarm = self.client.get('/api/alarm-incidents')
        self.assertEqual(alarm.headers['Cache-Control'], 'no-store')
        self.assertEqual(alarm.json()['account_id'], self.client.get('/api/me').json()['user']['id'])
        incident, = alarm.json()['incidents']
        self.assertEqual(incident['monitor_id'], mid)
        self.assertEqual(incident['monitor_name'], 'Checkout Website')
        self.assertEqual(incident['opening_evidence']['http_code'], 503)
        main.record_check(mid,failure)
        self.assertEqual(self.client.get('/api/alarm-incidents').json()['incidents'], [incident])
        with TestClient(main.app) as second_user:
            self.assertEqual(second_user.get('/api/alarm-incidents').status_code, 401)
            with patch.object(main,'send_email') as mail:
                r=second_user.post('/api/register',json={
                    'email':f'other-{os.urandom(5).hex()}@example.org',
                    'password':'OtherRandomPassword123!'},headers={'Origin':'http://testserver'})
            self.assertEqual(r.status_code,202)
            second_user.post('/api/verify-email',json={'token':re.search(r'#verify=([^\s]+)',mail.call_args.args[2]).group(1)},headers={'Origin':'http://testserver'})
            self.assertEqual(second_user.get('/api/dashboard').json()['notifications'],[])
            self.assertEqual(second_user.get('/api/alarm-incidents').json()['incidents'], [])

        main.record_check(mid,dict(state='up',http_code=200,response_ms=90,cert_days=60,error=None))
        notices=self.client.get('/api/dashboard').json()['notifications']
        self.assertEqual(len(notices),2)
        self.assertEqual(notices[0]['kind'],'recovery')
        self.assertEqual(self.client.get('/api/alarm-incidents').json()['incidents'], [])

    def test_public_status_reports_observed_monitor_checks(self):
        slug='public-'+os.urandom(5).hex()
        self.assertEqual(self.client.post('/api/status-page',headers=self.headers,
                         json={'title':'Real Site Status','slug':slug}).status_code,200)
        with patch.object(main,'validate_url',new=AsyncMock(return_value=('example.com','93.184.216.34',443,'/','https'))):
            added=self.client.post('/api/monitors',headers=self.headers,
                       json={'name':'My Homepage','url':'https://example.com','authorized':True})
        self.assertEqual(added.status_code,201,added.text)
        mid=added.json()['id']
        main.record_check(mid,dict(state='up',http_code=200,response_ms=90,cert_days=80,error=None))
        status=self.client.get('/api/public-status/'+slug).json()
        self.assertEqual(status['title'],'Real Site Status')
        self.assertEqual(status['monitors'][0]['name'],'My Homepage')
        self.assertEqual(status['monitors'][0]['state'],'up')
        self.assertNotIn('url',status['monitors'][0])
        self.assertEqual(self.client.get('/api/dashboard').json()['checks'][0]['http_code'],200)

    def test_public_branding_and_static_assets(self):
        page=self.client.get('/')
        self.assertEqual(page.status_code,200)
        self.assertIn('BeepNado',page.text)
        self.assertIn('Downtime hits.',page.text)
        self.assertNotIn('Pingcrest',page.text)
        self.assertEqual(self.client.get('/static/site.css').status_code,200)
        self.assertEqual(self.client.get('/static/app.js').status_code,200)
        self.assertEqual(self.client.get('/static/favicon.svg').status_code,200)
        self.assertEqual(self.client.get('/static/unlisted.html').status_code,404)
        self.assertEqual(self.client.get('/health').json()['name'],'BeepNado')

    def test_cross_origin_writes_blocked(self):
        r=self.client.post('/api/status-page',headers={
            'Origin':'https://attacker.example','X-CSRF-Token':self.csrf},
            json={'title':'Fraudulent status','slug':'fraudulent'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(self.client.get('/api/public-status/fraudulent').status_code,404)


    def test_registration_needs_email_and_verification(self):
        with TestClient(main.app) as visitor:
            other=f'new-{os.urandom(7).hex()}@example.org'
            with patch.object(main,'send_email') as mail:
                r=visitor.post('/api/register',json={'email':other,'password':'AnotherLongPassword123!'},headers={'Origin':'http://testserver'})
            self.assertEqual(r.status_code,202)
            self.assertFalse(visitor.get('/api/me').json()['signed_in'])
            self.assertEqual(visitor.post('/api/login',json={'email':other,'password':'AnotherLongPassword123!'},headers={'Origin':'http://testserver'}).status_code,403)
            token=re.search(r'#verify=([^\s]+)',mail.call_args.args[2]).group(1)
            self.assertEqual(visitor.post('/api/verify-email',json={'token':token},headers={'Origin':'http://testserver'}).status_code,200)
            self.assertTrue(visitor.get('/api/me').json()['signed_in'])
            self.assertEqual(visitor.post('/api/verify-email',json={'token':token},headers={'Origin':'http://testserver'}).status_code,400)

    def test_password_reset_invalidates_sessions(self):
        with patch.object(main,'send_email') as mail:
            result=self.client.post('/api/request-password-reset',json={'email':self.email},headers={'Origin':'http://testserver'})
        self.assertEqual(result.status_code,200)
        token=re.search(r'#reset=([^\s]+)',mail.call_args.args[2]).group(1)
        self.assertEqual(self.client.post('/api/reset-password',json={'token':token,'password':'NewLongPassword123!'},headers={'Origin':'http://testserver'}).status_code,200)
        self.assertEqual(self.client.get('/api/dashboard').status_code,401)
        self.assertEqual(self.client.post('/api/reset-password',json={'token':token,'password':'NewLongPassword123!'},headers={'Origin':'http://testserver'}).status_code,400)
        self.assertEqual(self.client.post('/api/login',json={'email':self.email,'password':'NewLongPassword123!'},headers={'Origin':'http://testserver'}).status_code,200)

    def test_registration_fails_closed_without_email(self):
        with patch.object(main,'SMTP_HOST',''):
            self.assertFalse(self.client.get('/api/availability').json()['free_registration'])
            with TestClient(main.app) as visitor:
                r=visitor.post('/api/register',json={'email':'no-smtp@example.org','password':'LongRandomPassword123!'},headers={'Origin':'http://testserver'})
                self.assertEqual(r.status_code,503)

    def test_unverified_accounts_cannot_access_private_data(self):
        with TestClient(main.app) as visitor:
            with patch.object(main,'send_email'):
                r=visitor.post('/api/register',json={'email':f'unverified-{os.urandom(6).hex()}@example.org','password':'LongRandomPassword123!'},headers={'Origin':'http://testserver'})
            self.assertEqual(r.status_code,202)
            self.assertEqual(visitor.get('/api/dashboard').status_code,401)

    def test_interactive_marketing_assets(self):
        html=self.client.get('/').text
        self.assertIn('experience.js',html)
        self.assertIn('account-forgot',html)
        self.assertEqual(self.client.get('/static/experience.css').status_code,200)
        self.assertEqual(self.client.get('/static/experience.js').status_code,200)
        self.assertEqual(self.client.get('/api/availability').status_code,200)

    def test_body_size_guard(self):
        r=self.client.post('/api/login',headers={'Content-Length':'70000','Origin':'http://testserver'},content='test')
        self.assertEqual(r.status_code,413)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_intervals_while_a_slow_probe_is_running(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        current = start - timedelta(seconds=1)
        finished = asyncio.Event()
        slow_stopped = asyncio.Event()
        probe_times = {}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return current

        async def advance(seconds):
            nonlocal current
            await asyncio.sleep(0)
            if current >= start + timedelta(seconds=300):
                finished.set()
                await asyncio.Event().wait()
            current += timedelta(seconds=seconds)

        async def probe(monitor_id):
            probe_times.setdefault(monitor_id, []).append(int((current - start).total_seconds()))
            if monitor_id == slow_id:
                try:
                    await asyncio.Event().wait()
                finally:
                    slow_stopped.set()
            with main.db() as c:
                c.execute('UPDATE monitors SET last_checked=? WHERE id=?', (main.now(), monitor_id))

        scheduler_asyncio = SimpleNamespace(sleep=advance, Semaphore=asyncio.Semaphore,
                                            create_task=asyncio.create_task, gather=asyncio.gather)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, 'DB_PATH', str(Path(directory) / 'scheduler.sqlite3')), \
             patch.object(main, 'datetime', Clock), patch.object(main, 'asyncio', scheduler_asyncio), \
             patch.object(main, 'check_monitor', probe):
            main.init_db()
            plan_ids = {}
            with main.db() as c:
                checked_at = start.isoformat()
                for plan in ('free', 'pro', 'team'):
                    user_id = c.execute('INSERT INTO users(email,password_hash,created_at,plan) VALUES(?,?,?,?)',
                                        (f'{plan}@example.test', 'unused', checked_at, plan)).lastrowid
                    plan_ids[plan] = c.execute('''INSERT INTO monitors(user_id,name,url,created_at,last_checked)
                        VALUES(?,?,?,?,?)''', (user_id, plan, 'https://example.test/', checked_at, checked_at)).lastrowid
                slow_user_id = c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',
                                         ('slow@example.test', 'unused', checked_at)).lastrowid
                slow_id = c.execute('INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)',
                                    (slow_user_id, 'slow', 'https://example.test/slow', checked_at)).lastrowid
            task = asyncio.create_task(main.scheduler())
            try:
                await asyncio.wait_for(finished.wait(), timeout=3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertEqual({plan: probe_times[monitor_id][0] for plan, monitor_id in plan_ids.items()},
                         {'free': 300, 'pro': 60, 'team': 30})
        self.assertEqual(probe_times[slow_id], [2])
        self.assertTrue(slow_stopped.is_set())


if __name__=='__main__':unittest.main()
