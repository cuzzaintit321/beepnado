#!/usr/bin/env python3
"""Configure IONOS mail and enable BeepNado signup on yeet after a delivery check."""
from __future__ import annotations

import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


# Send with the deployed application and report only allowlisted diagnostics.
MAIL_TEST = r'''
import json
import smtplib
import sys

from app.main import send_email

try:
    send_email(sys.argv[1], 'BeepNado delivery test',
               'This message verifies delivery from your BeepNado server.')
except smtplib.SMTPRecipientsRefused as exc:
    result = {'error': type(exc).__name__,
              'code': next(iter(exc.recipients.values()))[0]}
except (smtplib.SMTPException, OSError) as exc:
    result = {'error': type(exc).__name__, 'code': getattr(exc, 'smtp_code', None)}
else:
    result = {'ok': True}
print(json.dumps(result))
'''

APP = Path.home() / 'beepnado'
ENV = APP / '.env'
COMPOSE = ('docker', 'compose')
LOCAL = 'http://127.0.0.1:8490'
PUBLIC = 'https://app.beepnado.com'
EMAIL = re.compile(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}')


def compose(*args):
    return subprocess.run(
        (*COMPOSE, *args), cwd=APP, check=True, text=True, timeout=180,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout


def status(base):
    if base == PUBLIC:
        # Use the real TLS host and Caddy without depending on router NAT loopback.
        return json.loads(subprocess.check_output(
            ['curl', '-fsS', '--max-time', '8', '--resolve',
             'app.beepnado.com:443:127.0.0.1', PUBLIC + '/api/availability'],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        ))
    with urlopen(base + '/api/availability', timeout=8) as response:
        return json.load(response)


def wait_for_status(*, registration, email, billing):
    expected = {'free_registration': registration, 'email': email, 'billing': billing}
    for _ in range(25):
        try:
            with urlopen(LOCAL + '/health/ready', timeout=3):
                if status(LOCAL) == expected:
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('BeepNado did not report the expected readiness and capabilities.')


def write_private(path, contents, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.signup-', delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            tmp.write(contents)
            tmp.flush()
            os.fchmod(tmp.fileno(), mode)
            os.fsync(tmp.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def update(original, settings):
    # Quote literal values so Compose does not expand dollar signs in passwords.
    names = '|'.join(map(re.escape, settings))
    pattern = re.compile(rf'^\s*(?:export\s+)?(?:{names})\s*[=:]')
    lines = [line for line in original.decode().splitlines() if not pattern.match(line)]
    for key, value in settings.items():
        escaped = value.replace("'", "\\'")
        lines.append(f"{key}='{escaped}'")
    write_private(ENV, ('\n'.join(lines) + '\n').encode())
    effective = json.loads(compose('config', '--format', 'json'))['services']['web']['environment']
    if any(effective.get(key) != value for key, value in settings.items()):
        raise RuntimeError('Compose did not read the intended settings.')


def recreate(build=False):
    options = ['up', '-d', '--no-deps', '--force-recreate']
    if build:
        options.append('--build')
    compose(*options, 'web')


def add_email_date_header(source):
    if b"msg['Date'] = formatdate(usegmt=True)" in source:
        return source
    import_line = b'from email.message import EmailMessage\n'
    subject_line = b"    msg['Subject']=subject\n"
    if source.count(import_line) != 1 or source.count(subject_line) != 1:
        raise RuntimeError('Installed mailer differs from the expected version. No changes applied.')
    return source.replace(
        import_line, import_line + b'from email.utils import formatdate\n',
    ).replace(
        subject_line, subject_line + b"    msg['Date'] = formatdate(usegmt=True)\n",
    )


def send_test_email(recipient):
    result = json.loads(compose('exec', '-T', 'web', 'python', '-c', MAIL_TEST, recipient))
    if result.get('ok'):
        return
    name = result.get('error', 'UnknownError')
    code = result.get('code')
    suffix = f', SMTP {code}' if isinstance(code, int) else ''
    message = f'SMTP test failed ({name}{suffix}).'
    if name == 'SMTPAuthenticationError':
        message += ' Use the mailbox password. Check that the address can sign in to IONOS Webmail.'
    elif name in ('TimeoutError', 'ConnectionRefusedError', 'SMTPConnectError', 'SMTPServerDisconnected'):
        message += ' Retry once with the other SMTP port.'
    raise RuntimeError(message)


def stripe_audit():
    try:
        services = subprocess.check_output(
            ['docker', 'ps', '--format', '{{.Names}}\t{{.Label "com.docker.compose.project"}}'],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        )
        candidates = set()
        for service in services.splitlines():
            name, _, project = service.partition('\t')
            if re.search(r'reboot|syndicate', name + ' ' + project, re.I):
                candidates.add(name)
        if not candidates:
            print('Reboot Syndicate Stripe mode: no matching running container found.', flush=True)
        for name in sorted(candidates):
            environment = json.loads(subprocess.check_output(
                ['docker', 'inspect', '--format', '{{json .Config.Env}}', name],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            ))
            prefixes = [
                value.partition('_')[2].partition('_')[0]
                for entry in environment or []
                for key, _, value in [entry.partition('=')]
                if key.startswith('STRIPE_') and value.startswith(('sk_', 'rk_'))
            ]
            modes = {mode.upper() for mode in prefixes if mode in ('test', 'live')}
            print(f'Reboot Syndicate Stripe mode ({name}): {", ".join(sorted(modes)) or "undetected"}',
                  flush=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Reboot Syndicate Stripe mode could not be read.', flush=True)


def run():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RuntimeError('Run with ssh -t so the mailbox password can be entered privately.')
    if not ENV.is_file() or not (APP / 'compose.yaml').is_file() or ENV.is_symlink():
        raise RuntimeError('Expected a regular ~/beepnado/.env and compose.yaml on yeet.')

    previous = status(LOCAL)
    if status(PUBLIC) != previous:
        raise RuntimeError('Public and local BeepNado capabilities differ. Check Caddy first.')
    print('Current BeepNado capabilities:', json.dumps(previous), flush=True)
    stripe_audit()
    if previous['free_registration'] and previous['email']:
        print('Public signup and email are already enabled.')
        return

    mailbox = input('Existing IONOS mailbox address: ').strip()
    recipient = input('Your email address for the delivery test: ').strip()
    if not all(EMAIL.fullmatch(address) for address in (mailbox, recipient)):
        raise RuntimeError('Enter valid email addresses.')
    port = input('IONOS SMTP port [465, or 587]: ').strip() or '465'
    if port not in ('465', '587'):
        raise RuntimeError('Use port 465 for SSL/TLS or 587 for STARTTLS.')
    password = getpass.getpass('IONOS mailbox password (hidden): ')
    if not password or any(char in password for char in ('\n', '\r', '\0', '\\')):
        raise RuntimeError('This setup requires a nonempty, single-line password without backslashes.')

    original = ENV.read_bytes()
    source_path = APP / 'app' / 'main.py'
    original_source = source_path.read_bytes()
    patched_source = add_email_date_header(original_source)
    source_changed = patched_source != original_source
    source_mode = source_path.stat().st_mode & 0o777
    settings = {
        'SITE_URL': PUBLIC,
        'SESSION_SECURE': 'true',
        'REGISTRATION_ENABLED': 'false',
        'SMTP_HOST': 'smtp.ionos.com',
        'SMTP_PORT': port,
        'SMTP_USER': mailbox,
        'SMTP_PASSWORD': password,
        'SMTP_FROM': mailbox,
        'SMTP_SSL': 'true' if port == '465' else 'false',
        'SMTP_STARTTLS': 'true' if port == '587' else 'false',
    }
    restarted = False
    patched = False
    stage = 'checking the Compose SMTP settings'
    try:
        update(original, settings)
        if source_changed:
            stage = 'adding the Date header to the BeepNado mailer'
            write_private(source_path, patched_source, source_mode)
            patched = True
        stage = 'starting BeepNado with signup closed'
        print(f'{stage.capitalize()} (SMTP port {port})…', flush=True)
        restarted = True
        recreate(build=patched)
        wait_for_status(registration=False, email=True, billing=previous['billing'])

        stage = 'sending the SMTP test message from the BeepNado container'
        print(f'{stage.capitalize()}…', flush=True)
        send_test_email(recipient)
        print('SMTP accepted the test. Check your inbox and spam folder.', flush=True)
        if input('Type received once the test email arrives: ').strip().lower() != 'received':
            raise RuntimeError('Delivery was not confirmed.')

        stage = 'enabling and checking public signup'
        settings['REGISTRATION_ENABLED'] = 'true'
        update(original, settings)
        recreate()
        wait_for_status(registration=True, email=True, billing=previous['billing'])
        live = status(PUBLIC)
        if live != status(LOCAL):
            raise RuntimeError('The public HTTPS availability check did not match.')
    except (Exception, KeyboardInterrupt) as exc:
        try:
            write_private(ENV, original)
            if patched:
                write_private(source_path, original_source, source_mode)
            if restarted:
                recreate(build=patched)
        except (Exception, KeyboardInterrupt):
            print('Rollback could not restore BeepNado. Inspect Docker Compose on yeet.',
                  file=sys.stderr)
        else:
            print('Previous BeepNado configuration restored.', file=sys.stderr)
        if isinstance(exc, RuntimeError):
            raise RuntimeError(f'{stage.capitalize()}: {exc}') from None
        raise RuntimeError(f'Failed while {stage} ({type(exc).__name__}).') from None
    print('Public BeepNado capabilities:', json.dumps(live))
    print('Verify a fresh signup and password reset at https://app.beepnado.com/')


if __name__ == '__main__':
    try:
        if not APP.is_dir():
            raise RuntimeError('Run this on yeet, where ~/beepnado is installed.')
        with (APP / '.enable-signup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run()
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        elif isinstance(exc, BlockingIOError):
            print('A signup setup is already running.', file=sys.stderr)
        else:
            print(f'Setup stopped ({type(exc).__name__}).', file=sys.stderr)
        sys.exit(1)
