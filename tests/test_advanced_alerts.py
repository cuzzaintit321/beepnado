import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import main, integrations


class AdvancedAlerts(unittest.TestCase):
    def test_provider_validation(self):
        token='123456:abcdefghijklmnopqrstuvwxyz'
        self.assertEqual(integrations.endpoint('telegram',token+'|-12345'),token+'|-12345')
        with self.assertRaises(ValueError):
            integrations.endpoint('telegram',token)
        url='https://prod-01.westus.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=abc'
        self.assertEqual(integrations.endpoint('teams',url),url)
        with self.assertRaises(ValueError):
            integrations.endpoint('teams','https://evil.example/workflows/abc/triggers/manual/paths/invoke?sig=abc')
        self.assertEqual(integrations.payload('teams','subject','body')['type'],'message')

    def test_scoped_delivery_and_escalation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                uid=c.execute('INSERT INTO users(email,password_hash,created_at,email_verified_at) VALUES(?,?,?,?)',
                              ('alerts@example.net','unused',main.now(),main.now())).lastrowid
                mid=c.execute("INSERT INTO monitors(user_id,name,url,created_at,state) VALUES(?,?,?,?,'down')",
                              (uid,'Example','https://example.net',main.now())).lastrowid
                other=c.execute("INSERT INTO monitors(user_id,name,url,created_at) VALUES(?,?,?,?)",
                              (uid,'Other','https://example.org',main.now())).lastrowid
                iid=c.execute('INSERT INTO incidents(monitor_id,opened_at,summary) VALUES(?,?,?)',
                              (mid,'2020-01-01T00:00:00+00:00','Outage')).lastrowid
                def channel(label,monitor,delay):
                    return c.execute('INSERT INTO alert_channels(user_id,kind,label,endpoint,created_at,monitor_id,delay_minutes) VALUES(?,?,?,?,?,?,?)',
                        (uid,'discord',label,'https://discord.com/api/webhooks/123/token',main.now(),monitor,delay)).lastrowid
                immediate=channel('Immediate',mid,0)
                delayed=channel('Delayed',mid,10)
                unrelated=channel('Other',other,0)
                integrations.enqueue(c,uid,'Outage','Body',mid,iid)
                self.assertEqual([r[0] for r in c.execute('SELECT channel_id FROM channel_outbox')],[immediate])
                integrations.enqueue_escalations(c,main.SITE_URL,main.now(),main.ACTIVE_MONITORS)
                integrations.enqueue_escalations(c,main.SITE_URL,main.now(),main.ACTIVE_MONITORS)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM channel_outbox WHERE channel_id=?',(delayed,)).fetchone()[0],1)
                c.execute('UPDATE incidents SET acknowledged_at=? WHERE id=?',(main.now(),iid))
                late=channel('Late',mid,20)
                integrations.enqueue_escalations(c,main.SITE_URL,main.now(),main.ACTIVE_MONITORS)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM channel_outbox WHERE channel_id=?',(late,)).fetchone()[0],0)
                integrations.enqueue(c,uid,'Recovery','Body',mid,iid,True)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM channel_outbox WHERE channel_id=?',(delayed,)).fetchone()[0],2)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM channel_outbox WHERE channel_id=?',(unrelated,)).fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
