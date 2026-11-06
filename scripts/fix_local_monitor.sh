#!/usr/bin/env bash
# Restore the owner-hosted website route through Caddy on yeet.
set -Eeuo pipefail

if (( $# == 0 )); then
    exec ssh yeet 'cd "$HOME/beepnado" && bash -s -- --local' <"${BASH_SOURCE[0]}"
fi
if [[ $# != 1 || $1 != --local || ! -f compose.yaml || ! -f app/main.py ]]; then
    echo 'Run this script from Fedora, or run it with --local in the BeepNado directory on yeet.' >&2
    exit 2
fi

exec 9>.enable-signup.lock
flock -n 9 || { echo 'Another BeepNado update is running.' >&2; exit 1; }
lan_ip=192.168.1.130

check_caddy() {
    docker compose exec -T web python - "$1" <<'PY'
import asyncio
import sys

from app import main

domains = ('beepnado.com', 'rebootsyndicate.com', 'jasonpauledwards.com')
if sys.argv[1]:
    main.SELF_HOSTED_CADDY = sys.argv[1]


async def check(domain):
    url = f'https://{domain}/'
    host, public_ip, _, _, _ = await main.validate_url(url)
    if main.probe_connection_target(host, public_ip) != main.SELF_HOSTED_CADDY:
        raise RuntimeError(f'{domain} did not select the configured same-host route')
    result = await main.probe(url, expected_status=200)
    if result['state'] != 'up':
        raise RuntimeError(f"{domain}: {result['error'] or result.get('detail') or result['state']}")
    print(f'{domain} via {main.SELF_HOSTED_CADDY}: HTTPS {result["http_code"]}', flush=True)


async def verify():
    results = await asyncio.gather(*(check(domain) for domain in domains), return_exceptions=True)
    errors = [str(result) for result in results if isinstance(result, Exception)]
    if errors:
        raise SystemExit('\n'.join(errors))


asyncio.run(verify())
PY
}

if check_caddy ''; then
    echo 'All three local HTTPS checks are passing.'
    exit
fi

echo 'Checking the LAN route to Caddy from inside BeepNado.'
check_caddy "$lan_ip"

override=compose.override.yaml
settings=$(printf '%s\n' 'services:' '  web:' '    extra_hosts:' "      - 'jason-portfolio=$lan_ip'")
for file in compose.override.yaml compose.override.yml docker-compose.override.yaml docker-compose.override.yml; do
    if [[ -e "$file" || -L "$file" ]]; then
        if [[ "$file" != "$override" || -L "$file" ]] || ! cmp -s "$file" <(printf '%s\n' "$settings"); then
            echo "Existing Compose override requires a manual merge: $file" >&2
            exit 1
        fi
    fi
done

created=0
recreate_started=0
temporary=$(mktemp .local-caddy.XXXXXXXX)
trap 'rm -f -- "$temporary"' EXIT
rollback() {
    local status=$1
    trap - ERR INT TERM
    if (( created )); then rm -f -- "$override"; fi
    if (( recreate_started )) && ! docker compose up -d --no-deps --no-build --force-recreate --wait --wait-timeout 60 web; then
        echo 'The previous Compose file is restored. BeepNado needs a restart.' >&2
    else
        echo 'Restored the previous BeepNado Compose configuration.' >&2
    fi
    exit "$status"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

if [[ ! -e "$override" ]]; then
    printf '%s\n' "$settings" >"$temporary"
    mv -- "$temporary" "$override"
    created=1
fi

docker compose config --format json | python3 -c '
import json, sys
hosts = json.load(sys.stdin)["services"]["web"].get("extra_hosts", {})
ip = sys.argv[1]
if isinstance(hosts, dict):
    valid = hosts.get("jason-portfolio") in (ip, [ip])
else:
    valid = f"jason-portfolio={ip}" in hosts or f"jason-portfolio:{ip}" in hosts
if not valid:
    raise SystemExit("Compose did not load the local Caddy mapping")
' "$lan_ip"
recreate_started=1
docker compose up -d --no-deps --no-build --force-recreate --wait --wait-timeout 60 web
check_caddy ''
trap - ERR INT TERM
echo 'BeepNado can reach all three local sites again.'
