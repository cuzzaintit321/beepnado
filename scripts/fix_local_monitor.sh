#!/usr/bin/env bash
# Repair same-host monitoring of Reboot Syndicate via BeepNado's shared Caddy network.
# Run on Fedora AFTER git pull. Only the owner's exact domain is allowlisted.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="yeet"

echo '== Checking trusted local Caddy HTTPS from the monitor container =='
ssh "$HOST" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
cd "$HOME/beepnado"
docker compose exec -T web python - <<'PY'
import asyncio, ssl

async def check():
    reader, writer = await asyncio.wait_for(asyncio.open_connection(
        'jason-portfolio', 443, ssl=ssl.create_default_context(),
        server_hostname='rebootsyndicate.com'), timeout=10)
    try:
        writer.write(b'GET / HTTP/1.1\r\nHost: rebootsyndicate.com\r\nConnection: close\r\n\r\n')
        await asyncio.wait_for(writer.drain(), 5)
        line = await asyncio.wait_for(reader.readline(), 10)
        print('Local Caddy response:', line.decode(errors='replace').strip())
        assert line.startswith(b'HTTP/1.') and int(line.split()[1]) < 400, 'Caddy target is not healthy'
    finally:
        writer.close()
        await writer.wait_closed()

asyncio.run(check())
PY
REMOTE

echo '== Deploying tested BeepNado fix with source/SQLite backup =='
bash "$ROOT/scripts/push_upgrade_from_fedora.sh"

echo '== Enabling strictly allowlisted same-host monitoring =='
ssh "$HOST" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
cd "$HOME/beepnado"
cp -p .env ".env.before-self-monitor-$(date -u +%Y%m%dT%H%M%SZ)"
python3 - <<'PY'
from pathlib import Path
p=Path('.env')
lines=p.read_text().splitlines()
settings = {
    'SELF_HOSTED_PUBLIC_IP': '68.205.135.160',
    'SELF_HOSTED_DOMAINS': 'rebootsyndicate.com,www.rebootsyndicate.com'
}
for key, value in settings.items():
    for n, line in enumerate(lines):
        if line.startswith(key+'='):
            lines[n] = key+'='+value
            break
    else:
        lines.append(key+'='+value)
p.write_text('\n'.join(lines)+'\n')
PY
chmod 600 .env
docker compose up -d --force-recreate
READY=0
for _ in $(seq 1 20); do
    if curl -fsS --max-time 3 http://127.0.0.1:8490/health/ready >/dev/null 2>&1; then READY=1; break; fi
    sleep 2
done
if [[ "$READY" != 1 ]]; then
    echo 'BeepNado did not pass readiness. Restore the .env backup before continuing.' >&2
    exit 1
fi

docker compose exec -T web python - <<'PY'
import asyncio
from app.main import probe, db, check_monitor, probe_connection_target

assert probe_connection_target('rebootsyndicate.com','68.205.135.160') == 'jason-portfolio', 'Owner route not active'
assert probe_connection_target('other-site.com','68.205.135.160') == '68.205.135.160', 'Unsafe URL redirect'

async def run():
    result=await probe('https://rebootsyndicate.com/')
    print('Real monitor result:',result)
    if result['state'] != 'up':
        raise SystemExit('Check still failed. Inspect public DNS and Caddy.')
    with db() as conn:
        ids=[row[0] for row in conn.execute(
            'SELECT id FROM monitors WHERE url IN (?,?) AND enabled=1',
            ('https://rebootsyndicate.com','https://rebootsyndicate.com/'))]
    for monitor_id in ids:
        print('Saved monitor result:',await check_monitor(monitor_id))

asyncio.run(run())
PY
echo 'SUCCESS: BeepNado can monitor Reboot Syndicate on its local Caddy route.'
echo 'NOTE: Same-host checks do NOT detect home Internet/server-wide outages.'
REMOTE
