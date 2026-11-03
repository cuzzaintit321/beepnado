#!/usr/bin/env python3
"""Provision BeepNado's test-mode Stripe billing on yeet."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

APP = Path.home() / 'beepnado'
ENV = APP / '.env'
STATE = Path.home() / 'beepnado-backups' / 'stripe-test.json'
HOST = 'app.beepnado.com'
WEBHOOK = f'https://{HOST}/stripe/webhook'
EVENTS = ('customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted')
STRIPE_SETTINGS = ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_PRO',
                   'STRIPE_PRICE_TEAM', 'STRIPE_TEST_EMAIL')


def command(*args, input_text=None):
    result = subprocess.run(args, cwd=APP, input=input_text, text=True, timeout=180,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode}).')
    return result.stdout


def compose(*args):
    return command('docker', 'compose', *args)


def write_private(path, data):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f'Refusing to replace symlink {path.name}.')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        temp = Path(tmp.name)
        try:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def reboot_test_key():
    reboot = Path.home() / 'reboot-syndicate'
    if not reboot.is_dir():
        raise RuntimeError('Expected ~/reboot-syndicate on yeet.')
    service = subprocess.run(
        ('docker', 'compose', 'ps', '-q', 'website'), cwd=reboot, text=True, timeout=20,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
    ).stdout.strip().splitlines()
    if len(service) != 1 or not re.fullmatch(r'[a-f0-9]{12,64}', service[0]):
        raise RuntimeError('Expected one running Reboot Syndicate website container.')
    entries = json.loads(command('docker', 'inspect', '--format', '{{json .Config.Env}}', service[0]))
    env = dict(entry.split('=', 1) for entry in entries if '=' in entry)
    key = env.get('STRIPE_SECRET_KEY', '')
    if not re.fullmatch(r'(?:sk|rk)_test_[A-Za-z0-9_]+', key):
        raise RuntimeError('Reboot Syndicate must have a Stripe test-mode key.')
    return key


def stripe_request(key, resource, *, query=None, data=None, retry_key=None):
    url = f'https://api.stripe.com/v1/{resource}'
    if query:
        url += '?' + urlencode(query)
    headers = {'Authorization': 'Basic ' + base64.b64encode((key + ':').encode()).decode()}
    if retry_key:
        headers['Idempotency-Key'] = retry_key
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = Request(url, data=urlencode(data).encode() if data is not None else None, headers=headers)
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f'Stripe {resource.split("/")[0]} request failed (HTTP {exc.code}).') from None


def ensure_price(key, tier, amount):
    lookup = f'beepnado_{tier.lower()}_usd_month_v1'
    prices = stripe_request(key, 'prices', query=[('lookup_keys[]', lookup), ('limit', 2)])['data']
    if len(prices) > 1:
        raise RuntimeError(f'Found multiple Stripe test prices for {tier}.')
    if prices:
        price = prices[0]
    else:
        price = stripe_request(key, 'prices', retry_key=lookup, data={
            'lookup_key': lookup,
            'currency': 'usd',
            'unit_amount': amount,
            'recurring[interval]': 'month',
            'product_data[name]': f'BeepNado {tier}',
            'metadata[application]': 'beepnado',
        })
    recurring = price.get('recurring') or {}
    if not (price.get('active') and price.get('livemode') is False
            and price.get('lookup_key') == lookup and price.get('currency') == 'usd'
            and price.get('unit_amount') == amount and recurring.get('interval') == 'month'
            and recurring.get('interval_count') == 1):
        raise RuntimeError(f'Stripe returned an unexpected test price for {tier}.')
    return price['id']


def ensure_webhook(key):
    account = stripe_request(key, 'account')['id']
    if STATE.exists():
        if STATE.is_symlink() or STATE.stat().st_mode & 0o077:
            raise RuntimeError('The Stripe test state file must be private.')
        state = json.loads(STATE.read_bytes())
        if state.get('account') != account:
            raise RuntimeError('Saved BeepNado webhook belongs to another Stripe account.')
    else:
        endpoints = stripe_request(key, 'webhook_endpoints', query={'limit': 100})
        if endpoints.get('has_more') or any(item.get('url') == WEBHOOK for item in endpoints['data']):
            raise RuntimeError('An existing BeepNado test endpoint needs review in Stripe Workbench.')
        state = {'account': account, 'request': uuid4().hex}
        write_private(STATE, json.dumps(state).encode())

    if state.get('id'):
        endpoint = stripe_request(key, 'webhook_endpoints/' + state['id'])
    else:
        endpoint = stripe_request(key, 'webhook_endpoints', retry_key='beepnado-' + state['request'],
                                  data=[('url', WEBHOOK), ('description', 'BeepNado test subscriptions'),
                                        ('metadata[application]', 'beepnado'),
                                        *[('enabled_events[]', event) for event in EVENTS]])
    if (endpoint.get('url') != WEBHOOK or endpoint.get('livemode') is not False
            or endpoint.get('status') != 'enabled' or set(endpoint.get('enabled_events', [])) != set(EVENTS)):
        raise RuntimeError('Stripe returned an unexpected test webhook configuration.')
    signing = state.get('signing') or endpoint.get('secret', '')
    if not re.fullmatch(r'whsec_[A-Za-z0-9_]+', signing):
        raise RuntimeError('Stripe did not return the test webhook signing secret.')
    if not state.get('id'):
        state.update(id=endpoint['id'], signing=signing)
        write_private(STATE, json.dumps(state).encode())
    return signing


def app_environment():
    return json.loads(compose('config', '--format', 'json'))['services']['web']['environment']


def verify_account(email, *, checkout=False):
    result = compose('exec', '-T', 'web', 'python', '-c', '''
from app.main import billing_allowed, db
import sys
with db() as c:
    user = c.execute('SELECT email, email_verified_at FROM users WHERE email=?', (sys.argv[1],)).fetchone()
allowed = user and user['email_verified_at'] and (sys.argv[2] == 'false' or billing_allowed(user))
print('verified' if allowed else 'missing')
''', email, 'true' if checkout else 'false').strip()
    if result != 'verified':
        if checkout:
            raise RuntimeError('BeepNado did not enable checkout for the selected test account.')
        raise RuntimeError('Sign in with a verified BeepNado account before enabling test checkout.')


def check_webhook(signing):
    body = '{"id":"evt_beepnado_setup_probe","type":"beepnado.setup_probe","livemode":false,"data":{"object":{}}}'
    timestamp = int(time.time())
    signature = hmac.new(signing.encode(), f'{timestamp}.{body}'.encode(), hashlib.sha256).hexdigest()
    status = command('curl', '-sS', '--max-time', '10', '--resolve', f'{HOST}:443:127.0.0.1',
                     '-o', '/dev/null', '-w', '%{http_code}', '-H', 'Content-Type: application/json',
                     '-H', f'Stripe-Signature: t={timestamp},v1={signature}', '--data-binary', '@-',
                     WEBHOOK, input_text=body).strip()
    if status != '200':
        raise RuntimeError(f'BeepNado did not accept the signed test webhook probe (HTTP {status}).')


def run():
    if not sys.stdin.isatty() or not ENV.is_file() or ENV.is_symlink():
        raise RuntimeError('Run interactively on yeet with the existing BeepNado deployment.')
    email = input('Verified BeepNado email to allow for test checkout: ').strip().lower()
    if not re.fullmatch(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', email) or len(email) > 160:
        raise RuntimeError('Enter the email address of the account you will sign in with.')
    verify_account(email)
    previous = app_environment()
    key = reboot_test_key()
    if previous.get('STRIPE_SECRET_KEY') not in (None, '', key):
        raise RuntimeError('BeepNado already has a different Stripe key.')
    if any(previous.get(name) for name in STRIPE_SETTINGS[1:]) and not STATE.exists():
        raise RuntimeError('BeepNado already has Stripe settings that need review.')
    if previous.get('STRIPE_TEST_EMAIL') not in (None, '', email):
        raise RuntimeError('A different BeepNado test account is already configured.')

    print('Checking BeepNado test prices and webhook in Stripe…', flush=True)
    settings = {
        'STRIPE_SECRET_KEY': key,
        'STRIPE_PRICE_PRO': ensure_price(key, 'Pro', 1200),
        'STRIPE_PRICE_TEAM': ensure_price(key, 'Team', 3900),
        'STRIPE_WEBHOOK_SECRET': ensure_webhook(key),
        'STRIPE_TEST_EMAIL': email,
    }
    original = ENV.read_bytes()
    match = re.compile(r'^\s*(?:export\s+)?(?:' + '|'.join(STRIPE_SETTINGS) + r')\s*(?:=|:|$)')
    lines = [line for line in original.decode().splitlines() if not match.match(line)]
    lines += [f"{name}='{value}'" for name, value in settings.items()]
    restarted = False
    try:
        write_private(ENV, ('\n'.join(lines) + '\n').encode())
        effective = app_environment()
        if any(effective.get(name) != value for name, value in settings.items()):
            raise RuntimeError('Docker Compose did not read the intended Stripe settings.')
        restarted = True
        compose('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '75', 'web')
        verify_account(email, checkout=True)
        check_webhook(settings['STRIPE_WEBHOOK_SECRET'])
    except (Exception, KeyboardInterrupt):
        write_private(ENV, original)
        if restarted:
            compose('up', '-d', '--no-deps', '--force-recreate', 'web')
        print('Previous BeepNado environment restored. Stripe test resources can be reused.', file=sys.stderr)
        raise

    print('BeepNado test checkout configured. Signed webhook probe passed.')
    print(f'Sign in as {email} and open Plan & billing to test Pro or Team.')
    print('https://app.beepnado.com/#pricing')


if __name__ == '__main__':
    try:
        with (APP / '.enable-signup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run()
    except (Exception, KeyboardInterrupt) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else f'Setup stopped ({type(exc).__name__}).',
              file=sys.stderr)
        sys.exit(1)
