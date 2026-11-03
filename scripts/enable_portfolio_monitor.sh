#!/usr/bin/env bash
# Run on yeet. Extend the existing exact-domain Caddy route.
set -Eeuo pipefail
umask 077
cd "$HOME/beepnado"
exec 9>.enable-signup.lock
flock -n 9
domain=jasonpauledwards.com

verify() {
    docker compose exec -T web python - "$domain" "$1" <<'PY'
import asyncio, sys
from app import main as app

domain, phase = sys.argv[1:]
async def verify():
    if phase == 'preview':
        app.SELF_HOSTED_DOMAINS = app.SELF_HOSTED_DOMAINS | {domain}
    for host in ('rebootsyndicate.com', domain):
        url = f'https://{host}/'
        _, ip, _, _, _ = await app.validate_url(url)
        if app.probe_connection_target(host, ip) != app.SELF_HOSTED_CADDY:
            raise SystemExit(f'{host}: DNS and the existing Caddy route do not match')
        result = await app.probe(url)
        if result['state'] != 'up':
            raise SystemExit(f"{host}: {result['error']}")
        print(f"{host}: HTTP {result['http_code']} through Caddy ({phase})")

asyncio.run(verify())
PY
}

verify preview
state="$HOME/.local/state/beepnado"
mkdir -p "$state"
backup="$(mktemp "$state/portfolio-route.XXXXXXXX")"
cp -- .env "$backup"
recreate() { docker compose up -d --no-deps --no-build --force-recreate --wait --wait-timeout 90 web; }
rollback() {
    trap - ERR INT TERM
    cp -- "$backup" .env
    recreate || true
    echo "Restored the previous BeepNado configuration from $backup" >&2
    exit 1
}
trap rollback ERR INT TERM
python3 - "$domain" <<'PY'
from pathlib import Path
import re, sys

path = Path('.env')
source = path.read_text()
line = re.compile(r'^(?:export[ \t]+)?SELF_HOSTED_DOMAINS[ \t]*=[ \t]*([^\n]*)$', re.M)
matches = list(line.finditer(source))
if len(matches) != 1:
    raise SystemExit('Expected one SELF_HOSTED_DOMAINS line in BeepNado .env')
domains = matches[0][1].split(' #', 1)[0].strip().strip('"\'').split(',')
domains = [host.strip() for host in domains if host.strip()]
if sys.argv[1] not in domains:
    domains.append(sys.argv[1])
    path.write_text(line.sub('SELF_HOSTED_DOMAINS=' + ','.join(domains), source, count=1))
PY
recreate
curl -fsS --max-time 10 --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/health/ready >/dev/null
verify deployed
trap - ERR INT TERM
echo 'Portfolio route enabled. Use Check now on the existing monitor.'
