import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main, worker
from app.journeys import JourneySettings, JourneyStep, probe_journey


STEPS = [
    {'name': 'Home', 'path': '/', 'required_phrase': 'Home'},
    {'name': 'Pricing', 'path': '/pricing', 'required_phrase': 'Compare plans'},
    {'name': 'Contact', 'path': '/contact'},
]


class JourneyProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_in_order_and_stops_at_failed_step(self):
        config = JourneySettings(steps=STEPS).new_config()
        passed = dict(main.network_result(), http_code=200)
        failed = dict(main.network_result(error='Expected HTTP 200, received HTTP 503'), http_code=503)
        probe = AsyncMock(side_effect=[passed, failed])
        with patch.object(main, 'public_egress_available', AsyncMock(return_value=True)):
            observed = await probe_journey('https://example.net', config, probe, main.account_for_probe_network,
                                           public_only=True)
        self.assertEqual(observed['state'], 'down')
        self.assertEqual(observed['detail'], 'Step 2/3 · Pricing · Failed')
        self.assertEqual([step['name'] for step in observed['journey_trace']['steps']], ['Home', 'Pricing'])
        self.assertEqual(observed['journey_trace']['steps'][1]['http_code'], 503)
        self.assertEqual(probe.await_args_list[1].args, ('https://example.net/pricing',))
        self.assertEqual(probe.await_args_list[1].kwargs,
                         {'public_only': True, 'expected_status': 200, 'required_phrase': 'Compare plans'})

    async def test_real_http_responses_check_text_and_skip_later_pages(self):
        paths = []

        async def respond(reader, writer):
            request = await reader.readuntil(b'\r\n\r\n')
            method, path, _ = request.split(b'\r\n', 1)[0].split()
            paths.append((method, path))
            body = b'Home' if path == b'/' else b'Incorrect pricing content'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode()
                         + b'\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(respond, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]

        async def test_target(url):
            return 'example.net', '127.0.0.1', port, url.removeprefix('http://example.net'), 'http'

        async with server:
            with patch.object(main, 'validate_url', test_target), \
                 patch.object(main, 'public_egress_available', AsyncMock(return_value=True)):
                result = await main.run_probe('http://example.net', 'journey', JourneySettings(steps=STEPS).new_config())
        self.assertEqual(paths, [(b'GET', b'/'), (b'GET', b'/pricing')])
        self.assertEqual(result['state'], 'down')
        self.assertEqual(result['http_code'], 200)
        self.assertIn('Required phrase missing', result['journey_trace']['steps'][1]['error'])

    async def test_budget_and_network_failure_are_distinguished(self):
        config = JourneySettings(steps=STEPS).new_config()

        async def hanging_probe(*args, **kwargs):
            await asyncio.sleep(1)

        with patch('app.journeys.JOURNEY_TIMEOUT', 0.01), \
             patch.object(main, 'public_egress_available', AsyncMock(return_value=True)):
            timed_out = await probe_journey('https://example.net', config, hanging_probe,
                                            main.account_for_probe_network)
        self.assertEqual(timed_out['state'], 'down')
        self.assertIn('check budget', timed_out['error'])
        self.assertEqual(len(timed_out['journey_trace']['steps']), 1)

        with patch.object(main, 'public_egress_available', AsyncMock(return_value=False)):
            offline = await probe_journey(
                'https://example.net', config, AsyncMock(return_value=main.network_result(error='DNS timeout')),
                main.account_for_probe_network)
        self.assertEqual(offline['state'], 'unknown')
        self.assertEqual(offline['journey_trace']['steps'][0]['state'], 'unknown')

    async def test_worker_sends_the_current_trace(self):
        config = JourneySettings(steps=STEPS).new_config()
        probe = AsyncMock(return_value=dict(main.network_result(), http_code=200))
        with patch.object(worker, 'dashboard_request') as request, patch.object(main, 'probe', probe):
            request.side_effect = [
                {'targets': [{'id': 4, 'url': 'https://example.net', 'kind': 'journey', 'config': config}]},
                {'ok': True},
            ]
            await worker.sync_once('https://app.example.net', 'token', 'offsite')
        self.assertEqual(request.call_args_list[0].args[2], '/internal/targets?typed=true&journeys=true')
        result = request.call_args_list[1].args[3]
        self.assertEqual(result['state'], 'up')
        self.assertEqual(result['journey_trace']['revision'], config['revision'])
        self.assertEqual(len(result['journey_trace']['steps']), 3)

    def test_rejects_unsafe_step_paths(self):
        for path in ('//localhost/', 'https://example.net/', '/a?token=private', '/a#anchor',
                     '/a\\private', '/a\nHost: bad', '/bad%2', '/raw space'):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                JourneyStep(name='Test', path=path)


class JourneyApiTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(main, 'DB_PATH', str(Path(temp) / 'journeys.sqlite3')))
        self.enterContext(patch.object(main, 'WORKER_TOKEN', 'x' * 32))
        main.init_db()
        self.client, self.headers = self.account('journey@example.net')
        self.other, self.other_headers = self.account('other@example.net')

    def account(self, email):
        with main.db() as db:
            user_id = db.execute(
                'INSERT INTO users(email,password_hash,created_at,email_verified_at) VALUES(?,?,?,?)',
                (email, 'unused', main.now(), main.now()),
            ).lastrowid
            token = main.create_session(db, user_id)
            csrf = db.execute('SELECT csrf FROM sessions WHERE user_id=?', (user_id,)).fetchone()['csrf']
        client = TestClient(main.app)
        self.addCleanup(client.close)
        client.cookies.set(main.COOKIE, token)
        return client, {'X-CSRF-Token': csrf}

    def create_journey(self):
        data = {'name': 'Visitor journey', 'origin': 'https://example.net/', 'authorized': True, 'steps': STEPS}
        with patch.object(main, 'resolve_public', AsyncMock(return_value='1.1.1.1')):
            result = self.client.post('/api/monitors/journey', headers=self.headers, json=data)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['id']

    def test_incident_trace_survives_recovery_and_editing(self):
        mid = self.create_journey()

        async def check(url, **kwargs):
            code = 503 if url.endswith('/pricing') else 200
            return dict(main.network_result(error='HTTP 503' if code == 503 else None), http_code=code)

        with patch.object(main, 'probe', check):
            asyncio.run(main.check_monitor(mid))
            self.assertEqual(self.client.get('/api/alarm-incidents').json()['incidents'], [])
            asyncio.run(main.check_monitor(mid))
        opening = self.client.get('/api/alarm-incidents').json()['incidents'][0]['opening_evidence']
        self.assertEqual(opening['journey_trace']['steps'][1]['name'], 'Pricing')
        self.assertEqual(opening['journey_trace']['steps'][1]['http_code'], 503)

        with patch.object(main, 'probe', AsyncMock(return_value=dict(main.network_result(), http_code=200))):
            asyncio.run(main.check_monitor(mid))
        data = self.client.get('/api/dashboard').json()
        incident = data['incidents'][0]
        self.assertEqual(len(incident['recovery_evidence']['journey_trace']['steps']), 3)
        self.assertEqual(incident['opening_evidence'], opening)
        self.assertEqual(data['checks'][0]['journey_trace']['steps'][2]['name'], 'Contact')
        self.assertEqual(self.client.get('/api/alarm-incidents').json()['incidents'], [])
        self.assertEqual(self.other.get('/api/dashboard').json()['monitors'], [])

        endpoint = f'/api/monitors/{mid}/journey'
        steps = [STEPS[0], STEPS[2]]
        self.assertEqual(self.other.patch(endpoint, headers=self.other_headers, json={'steps': steps}).status_code, 404)
        self.assertEqual(self.client.patch(endpoint, json={'steps': steps}).status_code, 403)
        self.assertEqual(self.client.patch(endpoint, headers=self.headers, json={'steps': steps}).status_code, 200)
        edited = self.client.get('/api/dashboard').json()
        self.assertEqual(edited['monitors'][0]['state'], 'pending')
        self.assertEqual(edited['incidents'][0]['opening_evidence'], opening)

    def test_worker_compatibility_and_stale_results(self):
        mid = self.create_journey()
        auth = {'X-Worker-Token': 'x' * 32}
        for suffix in ('', '?typed=true'):
            self.assertEqual(self.client.get('/internal/targets' + suffix, headers=auth).json()['targets'], [])
        target, = self.client.get('/internal/targets?typed=true&journeys=true', headers=auth).json()['targets']
        config = target['config']
        report = {
            'monitor_id': mid, 'region': 'offsite', 'state': 'down', 'http_code': 503,
            'journey_trace': {'revision': config['revision'], 'steps': [
                {'name': 'Home', 'path': '/', 'state': 'down', 'http_code': 503, 'response_ms': 42},
            ]},
        }
        self.assertEqual(self.client.post('/internal/result', headers=auth, json=report).status_code, 200)
        data = self.client.get('/api/dashboard').json()
        self.assertEqual(data['monitors'][0]['regional_checks'][0]['journey_trace'], report['journey_trace'] | {
            'steps': [report['journey_trace']['steps'][0] | {'error': None}],
        })
        self.assertEqual(data['incidents'], [])
        self.assertEqual(self.client.patch(f'/api/monitors/{mid}/journey', headers=self.headers,
                                           json={'steps': [STEPS[0], STEPS[2]]}).status_code, 200)
        self.assertEqual(self.client.post('/internal/result', headers=auth, json=report).status_code, 409)
        self.assertEqual(self.client.get('/api/dashboard').json()['monitors'][0]['regional_checks'], [])

    def test_rejects_private_origin_and_cross_host_step(self):
        data = {'name': 'Visitor journey', 'origin': 'https://example.net/', 'authorized': True, 'steps': STEPS}
        with patch.object(main, 'resolve_public', AsyncMock(side_effect=ValueError('private address'))):
            result = self.client.post('/api/monitors/journey', headers=self.headers, json=data)
        self.assertEqual(result.status_code, 422)
        bad = data | {'steps': [STEPS[0], STEPS[1] | {'path': '//192.168.1.130/'}]}
        self.assertEqual(self.client.post('/api/monitors/journey', headers=self.headers, json=bad).status_code, 422)


if __name__ == '__main__':
    unittest.main()
