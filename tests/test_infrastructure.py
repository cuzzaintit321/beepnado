import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app import main, infrastructure


class InfrastructureTests(unittest.TestCase):
    def test_registration_ingestion_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            events=[]
            def notify(c,uid,subject,body):
                events.append((uid,subject))
            with main.db() as c:
                def user(email):
                    return c.execute('INSERT INTO users(email,password_hash,created_at,email_verified_at) VALUES(?,?,?,?)',
                                     (email,'unused',main.now(),main.now())).lastrowid
                uid=user('one@example.net')
                other=user('two@example.net')
                registered=infrastructure.register(c,uid,infrastructure.ServerInput(name='Test server'))
                sid,token=registered['id'],registered['token']
                with self.assertRaises(HTTPException):
                    infrastructure.register(c,uid,infrastructure.ServerInput(name='Second server'))
                self.assertNotEqual(c.execute('SELECT token_hash FROM infrastructure_servers').fetchone()[0],token)
                metrics=infrastructure.Metrics(platform='linux',hostname='test',cpu_percent=95,memory_percent=20,cpu_count=2,uptime_seconds=100)
                with self.assertRaises(HTTPException):
                    infrastructure.ingest(c,'x'*43,metrics,notify)
                for clock in (1000,1060,1120):
                    with patch.object(infrastructure.time,'time',return_value=clock):
                        infrastructure.ingest(c,token,metrics,notify)
                self.assertEqual(len(events),1)
                with patch.object(infrastructure.time,'time',return_value=1121):
                    with self.assertRaises(HTTPException):
                        infrastructure.ingest(c,token,metrics,notify)
                metrics.cpu_percent=10
                for clock in (1180,1240,1300):
                    with patch.object(infrastructure.time,'time',return_value=clock):
                        infrastructure.ingest(c,token,metrics,notify)
                self.assertEqual(len(events),2)
                self.assertEqual(len(infrastructure.history(c,uid,sid)),6)
                self.assertEqual(infrastructure.list_servers(c,other),[])
                with self.assertRaises(HTTPException):
                    infrastructure.history(c,other,sid)
                self.assertNotIn('token_hash',infrastructure.list_servers(c,uid)[0])


if __name__=='__main__':
    unittest.main()
