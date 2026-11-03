"""Bootstrap an owner account from an interactive Docker terminal, no public bypass.

Run only from a trusted server shell:
    docker compose exec web python -m app.bootstrap_owner
The owner has a verified email flag because the operator controls this local CLI.
Public registration remains disabled unless independently configured with SMTP.
"""
from __future__ import annotations
import getpass
import re
import sys
from . import main


def bootstrap() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('An interactive terminal is required. Do not pass passwords in shell arguments.', file=sys.stderr)
        return 2
    email = input('Owner email: ').strip().lower()
    if len(email) > 160 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        print('Invalid email address.', file=sys.stderr)
        return 2
    main.init_db()
    with main.db() as c:
        existing=c.execute('SELECT id,email_verified_at FROM users WHERE email=?',(email,)).fetchone()
    if existing and existing['email_verified_at']:
        print('This account is already active. Sign in instead; no password was changed.')
        return 1
    if existing:
        print('This pending account will be activated locally. Its password will be replaced.')
    else:
        print('Creating a Free owner account (2 monitors). No public signup will be enabled.')
    password=getpass.getpass('New password (at least 12 characters): ')
    verify=getpass.getpass('Confirm password: ')
    if not (12 <= len(password) <= 200) or password != verify:
        print('Password must be 12-200 characters and match confirmation.', file=sys.stderr)
        return 2
    with main.db() as c:
        if existing:
            c.execute('UPDATE users SET password_hash=?,email_verified_at=? WHERE id=?',
                      (main.make_password(password),main.now(),existing['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?',(existing['id'],))
            c.execute('DELETE FROM auth_tokens WHERE user_id=?',(existing['id'],))
        else:
            if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= main.MAX_USERS:
                print('Beta account cap reached. Increase MAX_USERS only if appropriate.', file=sys.stderr)
                return 2
            c.execute('INSERT INTO users(email,password_hash,created_at,email_verified_at) VALUES(?,?,?,?)',
                      (email,main.make_password(password),main.now(),main.now()))
    print('Owner account ready. Sign in at '+main.SITE_URL)
    print('SMTP is still required before enabling public registration or emailed alerts.')
    return 0


if __name__ == '__main__':
    raise SystemExit(bootstrap())
