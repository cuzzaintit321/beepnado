import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app import main,management as m


class ManagementTests(unittest.TestCase):
    def test_keys_scope_rate_and_tenant_isolation(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                def account(email,plan):
                    return c.execute('INSERT INTO users(email,password_hash,created_at,email_verified_at,plan) VALUES(?,?,?,?,?)',
                        (email,'unused',main.now(),main.now(),plan)).lastrowid
                owner=account('owner@example.net','pro')
                other=account('other@example.net','free')
                body=m.KeyInput(name='CI automation',scopes={'monitors:read'})
                with self.assertRaises(HTTPException):
                    m.create(c,owner,other,body)
                key=m.create(c,owner,owner,body)
                auth='Bearer '+key['token']
                self.assertEqual(m.authenticate(c,auth,'monitors:read')['id'],owner)
                with self.assertRaises(HTTPException) as err:
                    m.authenticate(c,auth,'monitors:write')
                self.assertEqual(err.exception.status_code,403)
                self.assertNotIn('token_hash',m.list_keys(c,owner)[0])
                self.assertNotIn(key['token'],str(m.list_keys(c,owner)))
                ids=[]
                for uid in (owner,owner,other):
                    ids.append(c.execute('INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)',
                        (uid,'Example','https://example.net',main.now())).lastrowid)
                first=m.monitors(c,owner,0,1)
                self.assertEqual(first['next_after'],ids[0])
                self.assertEqual(m.monitors(c,owner,first['next_after'],1)['items'][0]['id'],ids[1])
                with self.assertRaises(HTTPException):
                    m.one_monitor(c,owner,ids[2])
                with self.assertRaises(HTTPException):
                    m.checks(c,owner,ids[2],9223372036854775807,10)
                with patch.dict(m.RATES,{'pro':1}):
                    with self.assertRaises(HTTPException) as err:
                        m.authenticate(c,auth,'monitors:read')
                    self.assertEqual(err.exception.status_code,429)
                with self.assertRaises(HTTPException):
                    m.revoke(c,other,other,key['id'])
                m.revoke(c,owner,owner,key['id'])
                with self.assertRaises(HTTPException):
                    m.authenticate(c,auth,'monitors:read')

    def test_expiry_and_downgrade(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                uid=c.execute("INSERT INTO users(email,password_hash,created_at,email_verified_at,plan) VALUES(?,?,?,?,?)",
                    ('api@example.net','unused',main.now(),main.now(),'pro')).lastrowid
                body=m.KeyInput(name='Read',scopes={'checks:read'})
                one=m.create(c,uid,uid,body)
                two=m.create(c,uid,uid,body)
                c.execute("UPDATE users SET plan='free' WHERE id=?",(uid,))
                with self.assertRaises(HTTPException):
                    m.authenticate(c,'Bearer '+two['token'],'checks:read')
                c.execute('UPDATE management_keys SET expires_at=0 WHERE id=?',(one['id'],))
                with self.assertRaises(HTTPException):
                    m.authenticate(c,'Bearer '+one['token'],'checks:read')


if __name__=='__main__':
    unittest.main()
