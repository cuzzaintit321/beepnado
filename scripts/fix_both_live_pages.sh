#!/usr/bin/env bash
# Run on yeet through ssh. Fixes both BeepNado landing pages, NOT other sites.
# Preserves existing users, data volume, .env secrets, Stripe, and Caddyfile.
set -Eeuo pipefail
APP="$HOME/beepnado"
PUBLIC="$HOME/portfolio/site/beepnado/index.html"
APPHTML="$APP/app/static/index.html"
[[ -f "$PUBLIC" && -f "$APPHTML" && -f "$APP/compose.yaml" ]] || {
  echo 'Expected BeepNado HTML or app folder missing; nothing changed.' >&2
  exit 1
}
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/beepnado-backups/fix-both-pages-$STAMP"
mkdir -p "$BACKUP"
cp -p "$PUBLIC" "$BACKUP/public-index.html"
cp -p "$APPHTML" "$BACKUP/app-index.html"

python3 - "$PUBLIC" "$APPHTML" <<'PY'
from pathlib import Path
import os,re,sys
paths=[Path(x) for x in sys.argv[1:]]
updates={}
for page in paths:
    source=page.read_text(encoding='utf-8')
    if 'beepnado' not in source.lower() or 'id="pricing"' not in source or 'id="how"' not in source:
        raise SystemExit('Unexpected BeepNado layout in '+str(page)+'. No files changed.')
    html=source
    patterns=[
        # Complete broken feature section, including the unstyled Investigator.
        r'<section\b(?=[^>]*\bid\s*=\s*["\x27]features["\x27])[^>]*>.*?</section\s*>',
        # Old experience script dynamically injected the FAQ/simulator.
        r'<script\b(?=[^>]*\bsrc\s*=\s*["\x27](?:/static/)?(?:experience|beep-ui)\.js(?:\?[^"\x27]*)?["\x27])[^>]*>\s*</script\s*>',
        r'<section\b(?=[^>]*(?:\bid\s*=\s*["\x27]faq["\x27]|\bclass\s*=\s*["\x27][^"\x27]*\bbeep-faq\b))[^>]*>.*?</section\s*>',
        r'<section\b(?=[^>]*(?:\bid\s*=\s*["\x27]simulator["\x27]|\bclass\s*=\s*["\x27][^"\x27]*\bbeep-sim\b))[^>]*>.*?</section\s*>',
        # No dead Features/Investigator navigation item.
        r'<a\b(?=[^>]*\bhref\s*=\s*["\x27]#features["\x27])[^>]*>.*?</a\s*>',
    ]
    for pattern in patterns:
        html=re.sub(pattern,'',html,flags=re.I|re.S)
    if 'id="pricing"' not in html or 'id="how"' not in html or 'bn-investigator-teaser' in html:
        raise SystemExit('HTML validation failed for '+str(page)+'. No files changed.')
    updates[page]=html
for page,new_html in updates.items():
    if new_html==page.read_text(encoding='utf-8'):
        print('Already clean:',page)
        continue
    temp=page.with_name('.beepnado-fixed-'+page.name)
    temp.write_text(new_html,encoding='utf-8')
    os.chmod(temp,page.stat().st_mode)
    os.replace(temp,page)
    print('Fixed:',page)
PY

echo '== Synchronizing BeepNado Docker image (database volume preserved) =='
cd "$APP"
docker compose config --quiet
docker compose up -d --build

echo '== Checking app readiness =='
READY=0
for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 http://127.0.0.1:8490/health/ready >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 2
done
if [[ "$READY" != 1 ]]; then
    echo "App not ready; inspect logs. HTML backup: $BACKUP" >&2
    exit 1
fi

echo '== Verifying actual HTTPS-served HTML from existing Caddy =='
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
curl -fsS --connect-timeout 5 --max-time 15 \
  --resolve beepnado.com:443:127.0.0.1 \
  "https://beepnado.com/?refresh=$STAMP" >"$TMPDIR/public.html"
curl -fsS --connect-timeout 5 --max-time 15 \
  --resolve app.beepnado.com:443:127.0.0.1 \
  "https://app.beepnado.com/?refresh=$STAMP" >"$TMPDIR/app.html"
python3 - "$TMPDIR/public.html" "$TMPDIR/app.html" <<'PY'
from pathlib import Path
import re,sys
for path in map(Path,sys.argv[1:]):
    html=path.read_text(encoding='utf-8')
    problems=[]
    if 'id="pricing"' not in html: problems.append('missing pricing section')
    if 'id="how"' not in html: problems.append('missing how-it-works section')
    if re.search(r'id=["\x27]features["\x27]',html,re.I): problems.append('old features present')
    if 'bn-investigator-teaser' in html: problems.append('broken Investigator present')
    if re.search(r'<script[^>]+(?:experience|beep-ui)\.js',html,re.I): problems.append('legacy dynamic demo script present')
    if problems: raise SystemExit(str(path)+' FAILED: '+', '.join(problems))
    print(('PUBLIC' if path.name=='public.html' else 'APP')+' HTML: clean (HTTP 200)')
PY
echo "SUCCESS: BeepNado's public and app landing pages are clean."
echo "Backups: $BACKUP"
echo 'Note: Public signup, email alerts, and paid billing still require SMTP and Stripe configuration.'
