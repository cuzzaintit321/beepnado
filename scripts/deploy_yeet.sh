#!/usr/bin/env bash
# BeepNado v2: install app upgrade beside existing static website on yeet.
# Must be run ON yeet, from the extracted beepnado-final source directory.
set -Eeuo pipefail
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TARGET="$HOME/beepnado"
CADDYFILE="$HOME/portfolio/Caddyfile"
WEBROOT="$HOME/portfolio/site/beepnado"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$HOME/beepnado-backups/$STAMP"
[[ -f "$SOURCE/app/main.py" && -f "$TARGET/compose.yaml" && -f "$CADDYFILE" && -f "$WEBROOT/index.html" ]] || { echo 'Expected BeepNado or Caddy files are missing.' >&2; exit 1; }
command -v docker >/dev/null && command -v python3 >/dev/null || { echo 'Docker and Python3 are required.' >&2;exit 1; }
docker network inspect portfolio_default >/dev/null || { echo 'Existing Caddy network portfolio_default not found.' >&2; exit 1; }
docker inspect jason-portfolio >/dev/null
mkdir -p "$BACKUP"
cp -a "$TARGET/app" "$BACKUP/app"
cp -p "$TARGET/compose.yaml" "$BACKUP/compose.yaml"
cp -p "$TARGET/Dockerfile" "$BACKUP/Dockerfile"
cp -p "$TARGET/requirements.txt" "$BACKUP/requirements.txt"
cp -p "$CADDYFILE" "$BACKUP/Caddyfile"
cp -p "$WEBROOT/index.html" "$BACKUP/index.html"
[[ -f "$TARGET/.env" ]] && cp -p "$TARGET/.env" "$BACKUP/.env"
for file in experience.js experience.css; do
  [[ ! -f "$WEBROOT/static/$file" ]] || cp -p "$WEBROOT/static/$file" "$BACKUP/$file"
done
# SQLite's online backup API captures a consistent database image in the existing Docker volume.
docker exec -i beepnado-web-1 python - "$STAMP" <<'PY' || { echo 'Database backup failed. Stopping before upgrade.' >&2; exit 1; }
import os,sqlite3,sys
from pathlib import Path
src=Path(os.environ.get('DATABASE_PATH','/data/beepnado.sqlite3'))
if not src.exists():raise RuntimeError('Existing database not found')
dst=src.parent / ('beepnado-before-v2-'+sys.argv[1]+'.sqlite3')
with sqlite3.connect(str(src)) as connection, sqlite3.connect(str(dst)) as backup:
    connection.backup(backup)
print('Database backup in persistent Docker volume:',dst)
PY
rollback(){
  echo 'Deploy failed; restoring the former application and Caddy config...' >&2
  rm -rf "$TARGET/app" && cp -a "$BACKUP/app" "$TARGET/app"
  cp -p "$BACKUP/compose.yaml" "$TARGET/compose.yaml"
  cp -p "$BACKUP/Dockerfile" "$TARGET/Dockerfile"
  cp -p "$BACKUP/requirements.txt" "$TARGET/requirements.txt"
  [[ -f "$BACKUP/.env" ]] && cp -p "$BACKUP/.env" "$TARGET/.env"
  cp -p "$BACKUP/Caddyfile" "$CADDYFILE"
  cp -p "$BACKUP/index.html" "$WEBROOT/index.html"
  for file in experience.js experience.css; do
    [[ -f "$BACKUP/$file" ]] && cp -p "$BACKUP/$file" "$WEBROOT/static/$file" || rm -f "$WEBROOT/static/$file"
  done
  (cd "$TARGET" && docker compose up -d --build) || true
  docker exec jason-portfolio caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true
  echo "Backup at $BACKUP" >&2
}
trap rollback ERR
cp -a "$SOURCE/app/." "$TARGET/app/"
cp -p "$SOURCE/Dockerfile" "$TARGET/Dockerfile"
cp -p "$SOURCE/requirements.txt" "$TARGET/requirements.txt"
cp -p "$SOURCE/compose.yaml" "$TARGET/compose.yaml"
[[ -f "$TARGET/.env" ]] || cp "$SOURCE/.env.example" "$TARGET/.env"
# Preserve all customer-provided secrets while migrating key public settings.
python3 - "$TARGET/.env" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text();lines=s.splitlines()
def setkey(k,v):
    global lines
    lines=[x for x in lines if not x.startswith(k+'=')]
    lines.append(f'{k}={v}')
setkey('SITE_URL','https://app.beepnado.com')
setkey('SESSION_SECURE','true')
if not any(x.startswith('REGISTRATION_ENABLED=') for x in lines):setkey('REGISTRATION_ENABLED','false')
if not any(x.startswith('MAX_USERS=') for x in lines):setkey('MAX_USERS','50')
p.write_text('\n'.join(lines)+'\n')
PY
# Specific trusted proxy: the live address of the Caddy container on the shared Docker network.
CADDY_IP=$(docker inspect jason-portfolio | python3 -c 'import json,sys;d=json.load(sys.stdin)[0];print(d["NetworkSettings"]["Networks"]["portfolio_default"]["IPAddress"])')
[[ "$CADDY_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Could not determine Caddy proxy IP' >&2; exit 1; }
python3 - "$TARGET/.env" "$CADDY_IP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); lines=[x for x in p.read_text().splitlines() if not x.startswith('TRUSTED_PROXY_IP=')]
lines.append('TRUSTED_PROXY_IP='+sys.argv[2]);p.write_text('\n'.join(lines)+'\n')
PY
(cd "$TARGET" && docker compose config --quiet && docker compose up -d --build)
# Verify app healthy before configuring public routing.
for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 http://127.0.0.1:8490/health >/dev/null; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:8490/health >/dev/null
# Ensure Caddy can resolve the app container over the shared network.
docker exec jason-portfolio sh -c 'wget -qO- http://beepnado-web-1:8000/health' | grep -q 'BeepNado'
cp -p "$SOURCE/app/static/experience.js" "$WEBROOT/static/experience.js"
cp -p "$SOURCE/app/static/experience.css" "$WEBROOT/static/experience.css"
python3 - "$WEBROOT/index.html" "$CADDYFILE" <<'PY'
from pathlib import Path
import re,sys
html,caddy=map(Path,sys.argv[1:]); s=html.read_text()
# Remove the earlier optional interactive layer to avoid duplicate demo panels.
s=re.sub(r'<link rel="stylesheet" href="/static/beep-ui.css[^>]*>', '',s)
s=re.sub(r'<script src="/static/beep-ui.js[^>]*></script>', '',s)
if '/static/experience.js' not in s:
    s=s.replace('</head>','<link rel="stylesheet" href="/static/experience.css?v=2"><script src="/static/experience.js?v=2" defer></script></head>',1)
# The first tier opens the customer app; paid tiers clearly remain proposed.
s=s.replace('<span class="btn btn-outline-dark">Coming soon</span>', '<a class="btn btn-outline-dark" href="https://app.beepnado.com/?signup=1">Start free ↗</a>',1)
s=s.replace('<span class="btn btn-outline-dark">Coming soon</span>', '<span class="btn btn-outline-dark">Planned tier</span>')
s=s.replace('Checkout is disabled until Stripe has been configured.', 'Free registration requires verified email. Paid checkout is not active yet.')
# Header CTA should point toward the customer app once it is configured.
s=s.replace('href="#pricing">Early access ↗', 'href="https://app.beepnado.com/?signup=1">Start free ↗')
html.write_text(s)
text=caddy.read_text()
block=re.search(r'(beepnado\.com, www\.beepnado\.com \{.*?\n\})',text,re.S)
if not block:raise RuntimeError('Existing public BeepNado Caddy block not found; stop')
changed=block.group(1).replace("default-src 'none'; style-src 'self';", "default-src 'none'; script-src 'self'; style-src 'self';")
text=text.replace(block.group(1),changed,1)
if 'app.beepnado.com {' not in text:
    text+='''\n# Customer workspace. TLS is automatic once IONOS DNS has app CNAME set.\napp.beepnado.com {\n    encode gzip\n    reverse_proxy beepnado-web-1:8000\n}\n'''
caddy.write_text(text)
PY
docker exec jason-portfolio caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec jason-portfolio caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
trap - ERR
printf '\nSUCCESS. Source backup: %s\n' "$BACKUP"
printf 'DNS required: IONOS CNAME app -> beepnado.com\n'
printf 'Local health: http://127.0.0.1:8490/health\n'
printf 'Public landing: https://beepnado.com\n'
printf 'Workspace: https://app.beepnado.com (after DNS and SMTP setup)\n'
printf 'Free signup stays disabled until verified SMTP and REGISTRATION_ENABLED=true.\n'
