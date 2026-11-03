#!/usr/bin/env bash
# Surgical fix for the public BeepNado landing page on yeet.
# Leaves the dashboard, SQLite, Caddy and all other websites untouched.
set -Eeuo pipefail
HOST=yeet
ssh "$HOST" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import re
import os
import shutil

page=Path.home()/'portfolio/site/beepnado/index.html'
if not page.is_file():
    raise SystemExit('Public BeepNado index.html missing; no changes made.')
source=page.read_text(encoding='utf-8')
if 'beepnado' not in source.lower() or 'id="pricing"' not in source:
    raise SystemExit('Unexpected public page; refusing to modify it.')
current=source
changes={}

def remove(label,pattern):
    global current
    current,count=re.subn(pattern,'',current,flags=re.I|re.S)
    changes[label]=count

# Remove the entire broken feature section from the screenshot.
remove('broken feature section',
       r'<section\b(?=[^>]*\bid\s*=\s*["\x27]features["\x27])[^>]*>.*?</section\s*>')

# Remove scripts that generated the unwanted simulated demo and FAQ.
# This does not alter app.beepnado.com or its signed-in dashboard.
remove('legacy demo/FAQ scripts',
       r'<script\b(?=[^>]*\bsrc\s*=\s*["\x27](?:/static/)?(?:experience|beep-ui)\.js(?:\?[^"\x27]*)?["\x27])[^>]*>\s*</script\s*>')

# Remove any previously written static copies of those unwanted sections.
remove('static FAQ section',
       r'<section\b(?=[^>]*(?:\bid\s*=\s*["\x27]faq["\x27]|\bclass\s*=\s*["\x27][^"\x27]*\bbeep-faq\b))[^>]*>.*?</section\s*>')
remove('static simulation section',
       r'<section\b(?=[^>]*(?:\bid\s*=\s*["\x27]simulator["\x27]|\bclass\s*=\s*["\x27][^"\x27]*\bbeep-sim\b))[^>]*>.*?</section\s*>')

# Remove broken links to the deleted features section.
remove('obsolete feature navigation',
       r'<a\b(?=[^>]*\bhref\s*=\s*["\x27]#features["\x27])[^>]*>.*?</a\s*>')

if 'id="how"' not in current or 'id="pricing"' not in current:
    raise SystemExit('Required sections missing after cleanup; no changes made.')
if 'bn-investigator-teaser' in current:
    raise SystemExit('Broken feature block still present; no changes made.')
if current==source:
    print('Public page already clean.')
else:
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=Path.home()/'beepnado-backups'/('public-cleanup-'+stamp)
    backup.mkdir(parents=True,exist_ok=True)
    shutil.copy2(page,backup/'index.html')
    temporary=page.with_name('.index.html.beepnado-cleanup')
    temporary.write_text(current,encoding='utf-8')
    os.chmod(temporary,page.stat().st_mode)
    os.replace(temporary,page)
    print('PUBLIC PAGE CLEANED. Backup:',backup/'index.html')
for label,count in changes.items():
    print(label+':',count)
PY
echo '== Checking public website (local HTTPS) =='
if curl -fsS --connect-timeout 5 --max-time 12 --resolve beepnado.com:443:127.0.0.1 -o /dev/null -w 'Public page HTTP %{http_code}\n' https://beepnado.com/; then
    echo 'Public landing responds.'
else
    echo 'Public HTTPS check failed; inspect Caddy separately.' >&2
fi
echo '== Checking BeepNado monitoring application =='
if curl -fsS --connect-timeout 3 --max-time 8 http://127.0.0.1:8490/health/ready; then
    echo
    echo 'Monitoring application is ready.'
else
    echo 'Monitoring application not ready; public-page cleanup completed.' >&2
fi
REMOTE
