"""Regression tests for operational owner signup and real measured status."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, bootstrap_owner


class LaunchReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_path=main.DB_PATH
        self.old_session_secure=main.SESSION_SECURE
        main.DB_PATH=str(Path(self.temp.name)/'launch-test.sqlite3')
        main.SESSION_SECURE=False
        main.init_db()
        self.email='owner@example.org'
        self.password='SafePassword!12345'

    def tearDown(self):
        main.DB_PATH=self.old_path
        main.SESSION_SECURE=self.old_session_secure
        self.temp.cleanup()

    def create_owner(self, password=None):
        with patch.object(sys.stdin,'isatty',return_value=True), patch.object(sys.stdout,'isatty',return_value=True), patch('builtins.input',return_value=self.email), patch('getpass.getpass',side_effect=[password or self.password,password or self.password]), patch('builtins.print'):
            return bootstrap_owner.bootstrap()

    def test_owner_can_sign_in_without_opening_public_registration(self):
        with patch.object(main,'REGISTRATION_ENABLED',False), patch.object(main,'SMTP_HOST',''):
            self.assertEqual(self.create_owner(),0)
            with TestClient(main.app) as client:
                self.assertFalse(client.get('/api/availability').json()['free_registration'])
                login=client.post('/api/login',json={'email':self.email,'password':self.password})
                self.assertEqual(login.status_code,200,login.text)
                me=client.get('/api/me').json()
                self.assertTrue(me['signed_in'])
                self.assertEqual(me['user']['plan'],'free')
                self.assertEqual(client.get('/api/dashboard').status_code,200)
                self.assertEqual(client.get('/health/ready').json()['database'],'ready')

    def test_existing_verified_owner_not_overwritten(self):
        self.assertEqual(self.create_owner(),0)
        self.assertEqual(self.create_owner(),1)
        with main.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users WHERE email=?',(self.email,)).fetchone()[0],1)

    def test_noninteractive_bootstrap_refused(self):
        with patch.object(sys.stdin,'isatty',return_value=False), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bootstrap_owner.bootstrap(),2)
        with main.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],0)

    def test_dashboard_check_success_percentages_from_actual_checks(self):
        self.assertEqual(self.create_owner(),0)
        with main.db() as c:
            uid=c.execute('SELECT id FROM users WHERE email=?',(self.email,)).fetchone()[0]
            mid=c.execute('INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)',(uid,'Website','https://example.com',main.now())).lastrowid
        with TestClient(main.app) as client:
            self.assertEqual(client.post('/api/login',json={'email':self.email,'password':self.password}).status_code,200)
            mon=client.get('/api/dashboard').json()['monitors'][0]
            self.assertEqual(mon['samples_30d'],0)
            self.assertIsNone(mon['success_rate_30d'])
            main.record_check(mid,dict(state='up',http_code=200,response_ms=100,cert_days=30,error=None))
            main.record_check(mid,dict(state='down',http_code=503,response_ms=60,cert_days=30,error='HTTP 503'))
            mon=client.get('/api/dashboard').json()['monitors'][0]
            self.assertEqual(mon['samples_30d'],2)
            self.assertEqual(mon['success_rate_30d'],50.0)


if __name__=='__main__': unittest.main()
