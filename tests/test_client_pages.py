import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class ClientPageTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(main, 'DB_PATH', str(Path(temp) / 'client-pages.sqlite3')))
        main.init_db()
        self.owner, self.owner_headers, owner_id = self.account('owner@example.net')
        self.other, self.other_headers, other_id = self.account('other@example.net')
        self.public = self.enterContext(TestClient(main.app))
        self.monitors = {}
        with main.db() as c:
            for name, user_id in [('Client site', owner_id), ('Internal site', owner_id), ('Other account', other_id)]:
                self.monitors[name] = c.execute(
                    'INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)',
                    (user_id, name, 'https://example.net', main.now()),
                ).lastrowid

    def account(self, email):
        with main.db() as c:
            user_id = c.execute('INSERT INTO users(email,password_hash,created_at,email_verified_at) VALUES(?,?,?,?)',
                                (email, 'unused', main.now(), main.now())).lastrowid
            token = main.create_session(c, user_id)
            csrf = c.execute('SELECT csrf FROM sessions WHERE user_id=?', (user_id,)).fetchone()['csrf']
        client = self.enterContext(TestClient(main.app))
        client.cookies.set(main.COOKIE, token)
        return client, {'X-CSRF-Token': csrf}, user_id

    def create_page(self, **values):
        data = {'slug': 'client-status', 'title': 'Client Status', 'monitor_ids': [self.monitors['Client site']], **values}
        response = self.owner.post('/api/client-pages', headers=self.owner_headers, json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return '/api/client-pages/' + str(response.json()['id']), data

    def test_draft_and_published_scope_survive_editing(self):
        legacy = self.owner.post('/api/status-page', headers=self.owner_headers,
                                 json={'slug': 'all-sites', 'title': 'All sites'})
        self.assertEqual(legacy.status_code, 200, legacy.text)
        page, data = self.create_page()
        self.assertEqual(self.public.get('/api/client-pages').status_code, 401)
        self.assertEqual(self.other.get('/api/client-pages').json(), [])
        self.assertEqual(self.public.get('/s/client-status').status_code, 404)
        self.assertEqual(self.public.get('/api/public-status/client-status').status_code, 404)
        self.assertEqual(self.other.get(page + '/report').status_code, 404)
        self.assertEqual(self.other.put(page, headers=self.other_headers, json=data).status_code, 404)
        self.assertEqual(self.other.delete(page, headers=self.other_headers).status_code, 404)
        self.assertEqual(self.owner.put(page, json=data).status_code, 403)

        with main.db() as c:
            for monitor_id in self.monitors.values():
                c.execute('INSERT INTO incidents(monitor_id,opened_at,summary) VALUES(?,?,?)',
                          (monitor_id, main.now(), 'Private troubleshooting note'))
        response = self.owner.put(page, headers=self.owner_headers,
                                  json={**data, 'monitor_ids': [self.monitors['Other account']], 'published': True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.public.get('/s/client-status').status_code, 404)
        response = self.owner.put(page, headers=self.owner_headers, json={**data, 'published': True})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.public.get('/api/public-status/client-status')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual([m['name'] for m in response.json()['monitors']], ['Client site'])
        self.assertEqual([i['monitor'] for i in response.json()['incidents']], ['Client site'])
        self.assertNotIn('Private troubleshooting note', response.text)
        self.assertEqual(self.public.get('/s/client-status').headers['X-Robots-Tag'], 'noindex')

        response = self.public.get('/api/public-status/all-sites')
        self.assertEqual([m['name'] for m in response.json()['monitors']], ['Client site', 'Internal site'])
        self.assertEqual({i['monitor'] for i in response.json()['incidents']}, {'Client site', 'Internal site'})
        self.assertEqual(self.owner.put(page, headers=self.owner_headers, json=data).status_code, 200)
        self.assertEqual(self.public.get('/api/public-status/client-status').status_code, 404)
        self.assertEqual(self.owner.delete(page, headers=self.owner_headers).status_code, 200)
        self.assertEqual(self.owner.get(page + '/report').status_code, 404)

    def test_report_counts_only_selected_monitors_and_retained_samples(self):
        page, _ = self.create_page(published=True)
        clock = datetime.now(timezone.utc)
        with main.db() as c:
            monitor = self.monitors['Client site']
            for days, region, state, ms in [
                (1, 'primary', 'up', 100), (1, 'primary', 'down', 900),
                (1, 'primary', 'degraded', None), (1, 'maintenance', 'up', 500),
                (1, 'eu-west', 'down', None), (15, 'primary', 'up', 300),
                (31, 'primary', 'up', 600), (1, 'heartbeat', 'up', None),
            ]:
                c.execute('INSERT INTO checks(monitor_id,region,checked_at,state,response_ms) VALUES(?,?,?,?,?)',
                          (monitor, region, (clock - timedelta(days=days)).isoformat(timespec='seconds'), state, ms))
            c.execute("INSERT INTO checks(monitor_id,region,checked_at,state) VALUES(?,'primary',?,'up')",
                      (self.monitors['Internal site'], main.now()))
            opened = (clock - timedelta(days=10)).isoformat(timespec='seconds')
            ended = (clock - timedelta(days=5)).isoformat(timespec='seconds')
            c.execute("INSERT INTO incidents(monitor_id,opened_at,resolved_at,summary) VALUES(?,?,?,'Failure')",
                      (monitor, opened, ended))
        response = self.owner.get(page + '/report?days=7')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json()['status_path'], '/s/client-status')
        self.assertEqual(response.json()['monitors'], [
            {'name': 'Client site', 'samples': 4, 'passed': 2, 'failed': 1, 'avg_ms': 100, 'incidents': 1},
        ])
        self.assertEqual(self.owner.get(page + '/report?days=30').json()['monitors'], [
            {'name': 'Client site', 'samples': 5, 'passed': 3, 'failed': 1, 'avg_ms': 200, 'incidents': 1},
        ])
        self.assertEqual(self.owner.get(page + '/report?days=90').status_code, 422)
        with main.db() as c:
            c.execute('DELETE FROM monitors WHERE id=?', (monitor,))
        self.assertEqual(self.owner.get(page + '/report').json()['monitors'], [])
        self.assertEqual(self.public.get('/api/public-status/client-status').json()['monitors'], [])

    def test_pages_reserve_shared_slugs_and_respect_plan_limit(self):
        _, data = self.create_page()
        legacy = self.other.post('/api/status-page', headers=self.other_headers,
                                 json={'slug': data['slug'], 'title': 'Other status'})
        self.assertEqual(legacy.status_code, 409)
        legacy = self.other.post('/api/status-page', headers=self.other_headers,
                                 json={'slug': 'other-status', 'title': 'Other status'})
        self.assertEqual(legacy.status_code, 200)
        response = self.owner.post('/api/client-pages', headers=self.owner_headers,
                                   json={**data, 'slug': 'other-status'})
        self.assertEqual(response.status_code, 409)
        self.create_page(slug='second-client')
        response = self.owner.post('/api/client-pages', headers=self.owner_headers,
                                   json={**data, 'slug': 'third-client'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
