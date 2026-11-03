#!/usr/bin/env python3
"""Prepare BeepNado's live Stripe configuration, then activate it separately."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from setup_stripe_test import (
    APP, ENV, HOST, app_environment, check_webhook, command, compose,
    ensure_price, ensure_webhook, stripe_request, write_private,
)

STATE = Path.home() / '.local' / 'state' / 'beepnado'
RELEASE_MAIN = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
LIVE_MAIN = APP / 'app' / 'main.py'
PREVIOUS_MAIN = '463d79e4917dab6a5874dc9a56081a997186bf3a486204f8a73475047bd62c3c'
GATE = 'STRIPE_LIVE_CHECKOUT_ENABLED'
PORTAL = 'STRIPE_PORTAL_CONFIGURATION'
LIVE_KEY = re.compile(r'(?:sk|rk)_live_[A-Za-z0-9_]+')
TEST_KEY = re.compile(r'(?:sk|rk)_test_[A-Za-z0-9_]+')
PORTAL_TAG = 'beepnado_live_v1'
LIVE_WEBHOOK_STATE = STATE / 'stripe-live-webhook.json'
PREPARED = STATE / 'stripe-live-prepared.json'
TRANSITION = STATE / 'stripe-live-transition.json'


def caddy_json(path):
    return json.loads(command(
        'curl', '-fsS', '--max-time', '12', '--resolve', f'{HOST}:443:127.0.0.1',
        f'https://{HOST}{path}',
    ))


def app_check(*, billing=False):
    if caddy_json('/health/ready').get('database') != 'ready':
        raise RuntimeError('BeepNado database readiness check failed.')
    flags = caddy_json('/api/availability')
    if not (flags.get('free_registration') is True and flags.get('email') is True):
        raise RuntimeError('Public signup and email must be enabled first. Run enable_public_signup.py.')
    if flags.get('billing') is not billing:
        raise RuntimeError('BeepNado public checkout did not report the expected state.')


def write_env(original, settings):
    names = '|'.join(map(re.escape, settings))
    pattern = re.compile(rf'^\s*(?:export\s+)?(?:{names})\s*(?:=|:|$)')
    lines = [line for line in original.decode().splitlines() if not pattern.match(line)]
    for name, value in settings.items():
        if not re.fullmatch(r'[A-Za-z0-9_]*', value):
            raise RuntimeError(f'Invalid value for {name}.')
        lines.append(f"{name}='{value}'")
    write_private(ENV, ('\n'.join(lines) + '\n').encode())
    effective = app_environment()
    if any(effective.get(name) != value for name, value in settings.items()):
        raise RuntimeError('Docker Compose did not read the intended billing settings.')


def recreate(*, build=False):
    args = ('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '90')
    compose(*args, *(['--build'] if build else []), 'web')


def backup_database():
    token = uuid4().hex
    container_path = f'/tmp/beepnado-launch-{token}.sqlite3'
    backup = STATE / f'before-live-{token}.sqlite3'
    restore = STATE / f'restore-check-{token}.sqlite3'
    try:
        compose('exec', '-T', 'web', 'python', '-c', '''
import sqlite3, sys
from contextlib import closing
from app.main import DB_PATH
with closing(sqlite3.connect(DB_PATH)) as source, closing(sqlite3.connect(sys.argv[1])) as target:
    source.backup(target)
''', container_path)
        compose('cp', f'web:{container_path}', str(backup))
        backup.chmod(0o600)
        from contextlib import closing
        with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as source, \
                closing(sqlite3.connect(restore)) as copy:
            source.backup(copy)
            if (copy.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                    or copy.execute('PRAGMA foreign_key_check').fetchone() is not None):
                raise RuntimeError('Database backup failed restore validation.')
            for table in ('users', 'monitors', 'checks'):
                copy.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
    finally:
        restore.unlink(missing_ok=True)
        compose('exec', '-T', 'web', 'python', '-c',
                'from pathlib import Path; import sys; Path(sys.argv[1]).unlink(missing_ok=True)',
                container_path)
    return backup


def test_links():
    return json.loads(compose('exec', '-T', 'web', 'python', '-c', '''
import json
from app.main import db
with db() as c:
    rows = c.execute("""SELECT id,plan,stripe_customer,stripe_subscription,subscription_status
                        FROM users WHERE stripe_customer IS NOT NULL
                           OR stripe_subscription IS NOT NULL OR plan!='free'""")
    print(json.dumps([dict(row) for row in rows]))
'''))


def verify_test_canceled(key):
    if not TEST_KEY.fullmatch(key):
        raise RuntimeError('Run the BeepNado Stripe test checkout and cancellation before preparing live billing.')
    links = test_links()
    if not links:
        raise RuntimeError('No completed cancellation is recorded. Run a test checkout and cancel it in Stripe.')
    for user in links:
        sub_id = user['stripe_subscription'] or ''
        if (user['plan'] != 'free' or user['subscription_status'] != 'canceled'
                or not re.fullmatch(r'sub_[A-Za-z0-9_]+', sub_id)):
            raise RuntimeError('Cancel the BeepNado test subscription now in Stripe test mode and confirm the app returns to Free.')
        sub = stripe_request(key, 'subscriptions/' + sub_id)
        if (sub.get('livemode') is not False or sub.get('status') != 'canceled'
                or sub.get('customer') != user['stripe_customer']):
            raise RuntimeError('Stripe did not confirm the BeepNado test cancellation.')
    return links


def clear_test_links(links):
    compose('exec', '-T', 'web', 'python', '-c', '''
import json, sys
from app.main import db
expected = json.loads(sys.argv[1])
with db() as c:
    c.execute('BEGIN IMMEDIATE')
    rows = [dict(row) for row in c.execute(
        """SELECT id,plan,stripe_customer,stripe_subscription,subscription_status
           FROM users WHERE stripe_customer IS NOT NULL OR stripe_subscription IS NOT NULL OR plan!='free'""")]
    if any(row not in expected for row in rows):
        raise RuntimeError('Billing links changed during launch staging')
    for row in rows:
        c.execute("""UPDATE users SET stripe_customer=NULL,stripe_subscription=NULL,subscription_status=NULL
                     WHERE id=?""", (row['id'],))
''', json.dumps(links))


def ensure_portal(key):
    resource = 'billing_portal/configurations'
    configurations = stripe_request(key, resource, query={'active': 'true', 'limit': 100})
    matches = [item for item in configurations['data'] if (item.get('metadata') or {}).get('beepnado') == PORTAL_TAG]
    if len(matches) > 1 or configurations.get('has_more'):
        raise RuntimeError('Review the existing Stripe portal configurations before continuing.')
    if matches:
        portal = matches[0]
    else:
        portal = stripe_request(key, resource, retry_key=PORTAL_TAG, data={
            'metadata[beepnado]': PORTAL_TAG,
            'business_profile[headline]': 'Manage your BeepNado subscription',
            'default_return_url': f'https://{HOST}/#pricing',
            'features[invoice_history][enabled]': 'true',
            'features[payment_method_update][enabled]': 'true',
            'features[subscription_cancel][enabled]': 'true',
            'features[subscription_cancel][mode]': 'at_period_end',
        })
    cancel = (portal.get('features') or {}).get('subscription_cancel') or {}
    if (portal.get('livemode') is not True or not portal.get('active')
            or cancel.get('enabled') is not True or cancel.get('mode') != 'at_period_end'):
        raise RuntimeError('The BeepNado live billing portal did not match the expected configuration.')
    return portal['id']


def probe_stripe_checkout(key, prices):
    for price in prices:
        session = stripe_request(key, 'checkout/sessions', data={
            'mode': 'subscription',
            'line_items[0][price]': price,
            'line_items[0][quantity]': 1,
            'success_url': f'https://{HOST}/?billing=return',
            'cancel_url': f'https://{HOST}/?billing=cancel',
        })
        expired = stripe_request(key, f"checkout/sessions/{session['id']}/expire", data={})
        if session.get('livemode') is not True or expired.get('status') != 'expired':
            raise RuntimeError('Could not validate and close a live checkout probe.')


def check_release():
    previous = LIVE_MAIN.read_bytes()
    staged = RELEASE_MAIN.read_bytes()
    if previous != staged and hashlib.sha256(previous).hexdigest() != PREVIOUS_MAIN:
        raise RuntimeError('The installed app/main.py differs from the expected release. Review local changes first.')
    return previous, staged


def install_stage(settings):
    original = ENV.read_bytes()
    previous, staged = check_release()
    build = previous != staged
    write_private(STATE / 'before-live.env', original)
    if build:
        write_private(STATE / 'before-live-main.py', previous)
    try:
        write_env(original, settings)
        if build:
            write_private(LIVE_MAIN, staged)
        recreate(build=build)
        app_check()
        check_webhook(settings['STRIPE_WEBHOOK_SECRET'], livemode=True)
    except (Exception, KeyboardInterrupt):
        write_private(ENV, original)
        if build:
            write_private(LIVE_MAIN, previous)
        recreate(build=build)
        raise


def live_config():
    env = app_environment()
    required = ['STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_PRO', 'STRIPE_PRICE_TEAM', PORTAL]
    if not all(env.get(name) for name in required) or not LIVE_KEY.fullmatch(env['STRIPE_SECRET_KEY']):
        raise RuntimeError('A staged live Stripe configuration is required.')
    if env.get('SITE_URL') != f'https://{HOST}' or str(env.get('SESSION_SECURE', '')).lower() != 'true':
        raise RuntimeError('BeepNado must use its public HTTPS URL and secure session cookies.')
    return env


def prepare():
    app_check()
    check_release()
    previous = app_environment()
    previous_key = previous.get('STRIPE_SECRET_KEY', '')
    if previous.get('SITE_URL') != f'https://{HOST}' or str(previous.get('SESSION_SECURE', '')).lower() != 'true':
        raise RuntimeError('BeepNado must use its public HTTPS URL and secure session cookies.')

    if LIVE_KEY.fullmatch(previous_key):
        if not TRANSITION.is_file():
            raise RuntimeError('Live keys are already installed outside this setup. Review the configuration first.')
        transition = json.loads(TRANSITION.read_bytes())
        env = live_config()
        account = stripe_request(previous_key, 'account')['id']
        if account != transition['account']:
            raise RuntimeError('The running live key belongs to a different Stripe account.')
        check_webhook(env['STRIPE_WEBHOOK_SECRET'], livemode=True)
    else:
        links = verify_test_canceled(previous_key)
        print('Test cancellation verified. Creating and restoring a database snapshot…', flush=True)
        backup = backup_database()
        key = getpass.getpass('Stripe live key from the Reboot Syndicate Stripe account (hidden): ').strip()
        if not LIVE_KEY.fullmatch(key):
            raise RuntimeError('Enter a Stripe live secret or restricted key.')
        account = stripe_request(key, 'account')['id']
        if input(f'Live Stripe account {account}. Type that account ID to confirm: ').strip() != account:
            raise RuntimeError('Stripe account confirmation did not match.')
        print('Preparing separate BeepNado prices, webhook, and billing portal…', flush=True)
        pro = ensure_price(key, 'Pro', 1200, livemode=True)
        team = ensure_price(key, 'Team', 3900, livemode=True)
        signing = ensure_webhook(key, state_path=LIVE_WEBHOOK_STATE, livemode=True)
        portal = ensure_portal(key)
        probe_stripe_checkout(key, (pro, team))
        transition = {'account': account, 'backup': str(backup), 'test_links': links}
        write_private(TRANSITION, json.dumps(transition).encode())
        install_stage({
            'STRIPE_SECRET_KEY': key, 'STRIPE_WEBHOOK_SECRET': signing, 'STRIPE_PRICE_PRO': pro,
            'STRIPE_PRICE_TEAM': team, 'STRIPE_TEST_EMAIL': '', PORTAL: portal, GATE: 'false',
        })
        env = live_config()

    clear_test_links(transition['test_links'])
    prepared = {name: env[name] for name in ('STRIPE_PRICE_PRO', 'STRIPE_PRICE_TEAM', PORTAL)}
    prepared.update(account=transition['account'], backup=transition['backup'])
    write_private(PREPARED, json.dumps(prepared).encode())
    print(f"Database snapshot passed restore validation: {prepared['backup']}")
    print('BeepNado live billing staged. Public checkout is still closed.')
    print('Run this script with activate when ready to accept real payments.')


def activate():
    app_check()
    env = live_config()
    if not PREPARED.is_file():
        raise RuntimeError('Run prepare successfully before activating live checkout.')
    prepared = json.loads(PREPARED.read_bytes())
    if (stripe_request(env['STRIPE_SECRET_KEY'], 'account')['id'] != prepared['account']
            or any(env[name] != prepared[name] for name in ('STRIPE_PRICE_PRO', 'STRIPE_PRICE_TEAM', PORTAL))
            or not Path(prepared['backup']).is_file() or test_links()):
        raise RuntimeError('Prepared billing settings or verified backup changed. Review before activation.')
    if hashlib.sha256(LIVE_MAIN.read_bytes()).digest() != hashlib.sha256(RELEASE_MAIN.read_bytes()).digest():
        raise RuntimeError('The installed billing code changed after preparation.')
    check_webhook(env['STRIPE_WEBHOOK_SECRET'], livemode=True)
    if input('After verifying signup and password reset, type ACTIVATE to accept real payments: ').strip() != 'ACTIVATE':
        raise RuntimeError('Live checkout remains closed.')
    original = ENV.read_bytes()
    try:
        write_env(original, {GATE: 'true'})
        recreate()
        app_check(billing=True)
    except (Exception, KeyboardInterrupt):
        write_private(ENV, original)
        recreate()
        raise
    print('BeepNado live checkout is open.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'activate'))
    action = parser.parse_args().action
    if not sys.stdin.isatty() or not ENV.is_file() or ENV.is_symlink() or not LIVE_MAIN.is_file() or LIVE_MAIN.is_symlink():
        raise RuntimeError('Run interactively on yeet with the existing BeepNado deployment.')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_mode & 0o077:
        raise RuntimeError('The BeepNado launch state directory must be private.')
    with (APP / '.enable-signup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (prepare if action == 'prepare' else activate)()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else f'Launch setup stopped ({type(exc).__name__}).',
              file=sys.stderr)
        sys.exit(1)
