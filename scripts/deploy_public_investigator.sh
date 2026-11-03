#!/usr/bin/env bash
# Update only BeepNado's public marketing section on yeet; no other Caddy sites.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="yeet"
[[ -f "$ROOT/app/static/index.html" && -f "$ROOT/app/static/monitor-theme.css" && -f "$ROOT/app/static/experience.js" ]] || { echo 'Missing release files' >&2; exit 1; }
scp "$ROOT/app/static/index.html" "$HOST:~/beepnado-public-source.html"
scp "$ROOT/app/static/monitor-theme.css" "$HOST:~/beepnado-public-theme.css"
scp "$ROOT/app/static/experience.js" "$HOST:~/beepnado-public-experience.js"
ssh "$HOST" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
cd "$HOME"
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import shutil

base=Path.home()/'portfolio/site/beepnado'
page=base/'index.html'
style=base/'static/site.css'
source=Path.home()/'beepnado-public-source.html'
source_style=Path.home()/'beepnado-public-theme.css'
source_experience=Path.home()/'beepnado-public-experience.js'
public_experience=base/'static/experience.js'
if not page.is_file() or not style.is_file():
    raise SystemExit('Public BeepNado landing page or stylesheet missing; nothing changed.')
src=source.read_text(encoding='utf-8')
a=src.find('<section id="features" class="bn-investigator-teaser">')
b=src.find('<section class="section how" id="how">',a)
if a<0 or b<0:
    raise SystemExit('New investigator markup not found; nothing changed.')
snippet=src[a:b]
snippet=snippet.replace('<button class="btn btn-accent" id="investigator-start">Open your monitoring workspace ↗</button>',
                        '<a class="btn btn-accent" href="https://app.beepnado.com/?signin=1">Open your monitoring workspace ↗</a>')
current=page.read_text(encoding='utf-8')
start=current.find('<section id="features"')
end=current.find('<section class="section how" id="how">',start)
if start<0 or end<0:
    raise SystemExit('Original feature section not found; nothing changed.')
current=current[:start]+snippet+current[end:]
current=current.replace('href="#features" data-scroll="features">Features','href="#features" data-scroll="features">Investigator')
css=source_style.read_text(encoding='utf-8')
marker='/* Evidence-based incident investigator: account UI and marketing showcase */'
at=css.find(marker)
if at<0:raise SystemExit('Investigator stylesheet missing; nothing changed.')
css_snippet=css[at:]
# Hide any FAQ injected by stale marketing JS, even if the script was cached.
css_snippet+='\n#faq,.beep-faq{display:none!important}\n'
css_snippet+='\n/* Old simulated demo intentionally hidden on the public site. */\n#simulator,.beep-sim{display:none!important}\n'
original_style=style.read_text(encoding='utf-8')
if marker in original_style:
    original_style=original_style[:original_style.index(marker)]
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup=Path.home()/'beepnado-backups'/('marketing-'+stamp)
backup.mkdir(parents=True,exist_ok=True)
shutil.copy2(page,backup/'index.html')
shutil.copy2(style,backup/'site.css')
if public_experience.is_file():
    shutil.copy2(public_experience,backup/'experience.js')
page.write_text(current,encoding='utf-8')
style.write_text(original_style.rstrip()+'\n\n'+css_snippet,encoding='utf-8')
# Remove the dynamic FAQ generator if the public site serves this asset.
if public_experience.is_file():
    public_experience.write_text(source_experience.read_text(encoding='utf-8'),encoding='utf-8')
    current=page.read_text(encoding='utf-8').replace('/static/experience.js"','/static/experience.js?v=4"')
    page.write_text(current,encoding='utf-8')
print('Updated BeepNado public marketing page. Backup:',backup)
print('Other Caddy websites and application database unchanged.')
PY
rm -f "$HOME/beepnado-public-source.html" "$HOME/beepnado-public-theme.css" "$HOME/beepnado-public-experience.js"
REMOTE
