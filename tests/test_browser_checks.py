import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app import main,browser_checks as b


class BrowserTests(unittest.TestCase):
    def test_lease_history_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                def user(email):
                    return c.execute('INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)',(email,'unused',main.now())).lastrowid
                uid=user('browser@example.net')
                other=user('other@example.net')
                body=b.BrowserInput(name='Example',url='https://example.net/',authorized=True)
                jid=b.register(c,uid,body)
                with self.assertRaises(HTTPException):
                    b.register(c,uid,body)
                job=b.claim(c)
                self.assertEqual(job['id'],jid)
                self.assertIsNone(b.claim(c))
                fake_jpeg=base64.b64encode(b'\xff\xd8\xffabc\xff\xd9').decode()
                # Format envelope test, not a real browser screenshot.
                item=b.BrowserResult(job_id=jid,lease=job['lease'],state='observed',screenshot=fake_jpeg,fingerprint=[0]*256)
                b.submit(c,item)
                with self.assertRaises(HTTPException):
                    b.submit(c,item)
                c.execute('UPDATE browser_jobs SET next_due=0 WHERE id=?',(jid,))
                job2=b.claim(c)
                b.submit(c,b.BrowserResult(job_id=jid,lease=job2['lease'],state='observed',screenshot=fake_jpeg,fingerprint=[255]*256))
                rows=b.history(c,uid,jid)
                self.assertEqual(rows[0]['visual_difference_percent'],100)
                self.assertTrue(rows[0]['has_screenshot'])
                self.assertNotIn('lease_hash',b.list_jobs(c,uid)[0])
                self.assertEqual(b.list_jobs(c,other),[])
                with self.assertRaises(HTTPException):
                    b.history(c,other,jid)

    def test_disabled_by_default(self):
        with patch.dict('os.environ',{'ENABLE_BROWSER_MONITORING':'false','BROWSER_WORKER_TOKEN':'x'*40}):
            self.assertFalse(main.browser_enabled())

if __name__=='__main__':
    unittest.main()
