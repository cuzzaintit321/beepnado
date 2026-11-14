import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app import main,teams


class TeamTests(unittest.TestCase):
    def test_invitation_roles_seats_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(main,'DB_PATH',str(Path(directory)/'test.sqlite3')):
            main.init_db()
            with main.db() as c:
                def account(email,plan='free'):
                    return c.execute('INSERT INTO users(email,password_hash,created_at,email_verified_at,plan) VALUES(?,?,?,?,?)',
                        (email,'unused',main.now(),main.now(),plan)).lastrowid
                owner=account('owner@example.net','pro')
                member=account('member@example.net')
                other=account('other@example.net')
                actor={'id':member,'email':'member@example.net','email_verified_at':main.now(),'csrf':'member_csrf','stripe_customer':'private','subscription_status':'active'}
                with self.assertRaises(HTTPException):
                    teams.scope(c,actor,str(owner))
                token,_=teams.invite(c,owner,owner,teams.InviteInput(email=actor['email'],role='viewer'))
                with self.assertRaises(HTTPException):
                    teams.accept(c,dict(actor,id=other,email='other@example.net'),token)
                self.assertEqual(teams.accept(c,actor,token),owner)
                with self.assertRaises(HTTPException):
                    teams.accept(c,actor,token)
                scoped=teams.scope(c,actor,str(owner))
                self.assertEqual(scoped['id'],owner)
                self.assertEqual(scoped['actor_id'],member)
                self.assertEqual(scoped['workspace_role'],'viewer')
                self.assertIsNone(scoped['stripe_customer'])
                self.assertEqual(scoped['csrf'],'member_csrf')
                self.assertFalse(teams.can_write('viewer','DELETE','/api/monitors/1'))
                teams.change_member(c,owner,owner,member,'operator')
                self.assertTrue(teams.can_write('operator','POST','/api/monitors/1/check'))
                self.assertFalse(teams.can_write('operator','DELETE','/api/monitors/1'))
                self.assertTrue(teams.can_write('admin','DELETE','/api/monitors/1'))
                self.assertFalse(teams.can_write('admin','POST','/api/billing/checkout'))
                self.assertFalse(teams.can_write('admin','POST','/api/infrastructure/1/rotate'))
                teams.change_member(c,owner,owner,member,'admin')
                with self.assertRaises(HTTPException):
                    teams.invite(c,owner,member,teams.InviteInput(email='other@example.net',role='admin'))
                teams.invite(c,owner,member,teams.InviteInput(email='other@example.net',role='viewer'))
                with self.assertRaises(HTTPException):
                    teams.invite(c,owner,owner,teams.InviteInput(email='fourth@example.net',role='viewer'))
                c.execute("UPDATE users SET plan='free' WHERE id=?",(owner,))
                with self.assertRaises(HTTPException):
                    teams.scope(c,actor,str(owner))
                c.execute("UPDATE users SET plan='pro' WHERE id=?",(owner,))
                self.assertEqual(teams.scope(c,actor,str(owner))['workspace_role'],'admin')
                teams.change_member(c,owner,owner,member,None)
                with self.assertRaises(HTTPException):
                    teams.scope(c,actor,str(owner))
                self.assertEqual(teams.scope(c,actor,None)['id'],member)
                self.assertGreater(c.execute('SELECT COUNT(*) FROM team_audit WHERE owner_id=?',(owner,)).fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
