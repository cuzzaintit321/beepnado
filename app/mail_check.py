"""Server-side SMTP delivery test with no password or token output.

Run: docker compose exec web python -m app.mail_check
"""
from __future__ import annotations
import sys
from . import main


def run() -> int:
    if not main.email_ready():
        print('SMTP not configured. Keep REGISTRATION_ENABLED=false until email sends successfully.')
        return 2
    if not sys.stdin.isatty():
        print('Run interactively on the server to enter the recipient email.')
        return 2
    recipient=input('Send test email to: ').strip()
    if not recipient or '@' not in recipient or len(recipient)>160 or '\n' in recipient:
        print('Enter a valid recipient address.')
        return 2
    try:
        main.send_email(recipient, 'BeepNado SMTP delivery test',
                        'BeepNado can send email. Verify that you received this test before enabling public registration.')
    except Exception as exc:
        print('Email test failed:', type(exc).__name__, '(credentials not displayed)')
        return 1
    print('SMTP accepted the message. Check the inbox and spam folder to confirm actual delivery.')
    return 0


if __name__=='__main__':
    raise SystemExit(run())
