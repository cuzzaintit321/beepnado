#!/usr/bin/env bash
# Recover the existing BeepNado production services on yeet without rebuilding,
# resetting SQLite, touching Stripe, or editing other Caddy site blocks.
set -Eeuo pipefail
APP="$HOME/beepnado"
WEBROOT="$HOME/portfolio/site/beepnado"
PAGE="$WEBROOT/index.html"
[[ -f "$APP/compose.yaml" && -f "$PAGE" ]] || {
  echo 'BeepNado app or public site missing. Nothing was modified.' >&2
  exit 1
}
cd "$APP"
echo '== Starting BeepNado application =='
docker compose config --quiet
docker compose up -d
if ! docker inspect jason-portfolio >/dev/null 2>&1; then
  echo 'Shared Caddy container jason-portfolio is missing; stopping without changing Caddy.' >&2
  exit 1
fi
if [[ "$(docker inspect -f '{{.State.Running}}' jason-portfolio)" != true ]]; then
  echo 'Starting existing shared Caddy container (no config changes)...'
  docker start jason-portfolio
fi

echo '== Removing broken marketing blocks =='
python3 - "$PAGE" <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import os, re, shutil, sys
p=Path(sys.argv[1])
s=p.read_text(encoding='utf-8')
if 'beepnado' not in s.lower() or 'id="pricing"' not in s or 'id="how"' not in s:
    raise SystemExit('Unexpected BeepNado public HTML; no public-page changes made')
a=s
patterns={
  'broken features':r'<section\b(?=[^>]*\bid\s*=\s*["\x27]features["\x27])[^>]*>.*?</section\s*>',
  'old JS-generated FAQ and demo':r'<script\b(?=[^>]*\bsrc\s*=\s*["\x27](?:/static/)?(?:experience|beep-ui)\.js(?:\?[^"\x27]*)?["\x27])[^>]*>\s*</script\s*>',
  'existing FAQ markup':r'<section\b(?=[^>]*(?:\bid\s*=\s*["\x27]faq["\x27]|\bclass\s*=\s*["\x27][^"\x27]*\bbeep-faq\b))[^>]*>.*?</section\s*>',
  'existing simulator markup':r'<section\b(?=[^>]*(?:\bid\s*=\s*["\x27]simulator["\x27]|\bclass\s*=\s*["\x27][^"\x27]*\bbeep-sim\b))[^>]*>.*?</section\s*>',
  'obsolete features nav':r'<a\b(?=[^>]*\bhref\s*=\s*["\x27]#features["\x27])[^>]*>.*?</a\s*>'}
for label,pat in patterns.items():
    s,n=re.subn(pat,'',s,flags=re.I|re.S)
    print(label+':',n)
if 'id="how"' not in s or 'id="pricing"' not in s or 'bn-investigator-teaser' in s:
    raise SystemExit('Public HTML integrity check failed; no public-page changes made')
if s!=a:
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=Path.home()/'beepnado-backups'/('recover-public-'+stamp)
    backup.mkdir(parents=True,exist_ok=True)
    shutil.copy2(p,backup/'index.html')
    temp=p.with_name('.beepnado-recovery.tmp')
    temp.write_text(s,encoding='utf-8')
    os.chmod(temp,p.stat().st_mode)
    os.replace(temp,p)
    print('Public page cleaned. Backup:',backup/'index.html')
else:
    print('Public page already clean.')
PY

echo '== Validating customer app =='
READY=0
for _ in $(seq 1 20); do
  if curl -fsS --max-time 4 http://127.0.0.1:8490/health/ready >/dev/null 2>&1; then READY=1; break; fi
  sleep 2
done
[[ "$READY" == 1 ]] || { echo 'Customer app failed readiness; see docker compose logs --tail=80 web' >&2; exit 1; }

echo '== Validating HTTPS routes through existing Caddy =='
curl -fsS --connect-timeout 5 --max-time 12 --resolve beepnado.com:443:127.0.0.1 \
  -o /dev/null -w 'PUBLIC PAGE: HTTP %{http_code}\n' https://beepnado.com/
curl -fsS --connect-timeout 5 --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 \
  -o /dev/null -w 'APP HEALTH: HTTP %{http_code}\n' https://app.beepnado.com/health/ready
curl -fsS --connect-timeout 5 --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 \
  https://app.beepnado.com/api/availability
printf '\nBEEPNADO RECOVERED (local checks). No customer database, credentials, or other sites changed.\n'
printf 'Note: public signup still depends on SMTP; subscriptions depend on configured, tested Stripe billing.\n'
