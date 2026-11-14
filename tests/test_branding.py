import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app import main, branding as b


class BrandingTests(unittest.TestCase):
    def test_tenant_plan_domain_and_subscriptions(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                def user(email,plan):
                    return c.execute('INSERT INTO users(email,password_hash,created_at,plan) VALUES(?,?,?,?)',(email,'unused',main.now(),plan)).lastrowid
                owner=user('owner@example.net','team')
                other=user('other@example.net','free')
                pid=c.execute('INSERT INTO status_pages(user_id,slug,title,created_at) VALUES(?,?,?,?)',(owner,'team-status','Team status',main.now())).lastrowid
                with self.assertRaises(HTTPException):
                    b.page(c,other,'primary',pid)
                with self.assertRaises(HTTPException):
                    b.save(c,owner,'primary',pid,b.BrandInput(hide_brand=True),'pro')
                b.save(c,owner,'primary',pid,b.BrandInput(brand_name='Company',hide_brand=True),'team')
                config,_,brand=b.public(c,owner,None)
                self.assertTrue(config['hide_brand'])
                self.assertFalse(config['show_groups'])
                domain=b.add_domain(c,owner,'primary',pid,'status.example.net','team')
                self.assertIsNone(b.domain_target(c,'status.example.net'))
                c.execute('UPDATE status_domains SET verified_at=?,activated_at=? WHERE id=?',(int(b.time.time()),int(b.time.time()),domain['id']))
                with patch.dict('os.environ',{'STATUS_CUSTOM_HOSTS':'status.example.net'}):
                    self.assertEqual(b.domain_target(c,'status.example.net'),'team-status')
                    c.execute("UPDATE users SET plan='free' WHERE id=?",(owner,))
                    self.assertIsNone(b.domain_target(c,'status.example.net'))
                    self.assertFalse(b.public(c,owner,None)[0]['hide_brand'])
                token=b.subscribe(c,brand['id'],'subscriber@example.net','team')
                self.assertIsNotNone(token)
                self.assertIsNone(b.subscribe(c,brand['id'],'subscriber@example.net','team'))
                email,unsubscribe=b.confirm(c,token)
                self.assertEqual(email,'subscriber@example.net')
                with self.assertRaises(HTTPException):
                    b.confirm(c,token)
                aid=c.execute('INSERT INTO status_announcements(brand_id,title,body,kind,created_at) VALUES(?,?,?,?,?)',(brand['id'],'Maintenance','Scheduled work','maintenance',int(b.time.time()))).lastrowid
                b.queue_announcement(c,brand['id'],aid,'team')
                self.assertIn(unsubscribe,b.mail_batch(c,'https://app.example.net')[0]['message'])
                b.unsubscribe(c,unsubscribe)
                self.assertEqual(b.mail_batch(c,'https://app.example.net'),[])
                with self.assertRaises(HTTPException):
                    b.decode_image('bm90IGEgcG5n','logo')


if __name__=='__main__':
    unittest.main()
