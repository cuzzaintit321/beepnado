#!/usr/bin/env bash
# Deploy only the version-controlled public marketing HTML and sitemap.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-yeet}"
cd "$ROOT/public"
python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
root=Path('.')
urls=ET.parse('sitemap.xml').getroot()
for item in urls:
    url=item[0].text
    assert url.startswith('https://beepnado.com/')
    path=root / url.removeprefix('https://beepnado.com/') / 'index.html'
    assert path.is_file(), path
    s=path.read_text()
    assert '<title>' in s and 'rel="canonical"' in s, path
    assert url in s, path
print('Validated',len(urls),'sitemap entries')
PY
# Stage HTML and dedicated tool assets; leave existing styles, app and Caddy intact.
STAGE=$(ssh "$HOST" 'mktemp -d "$HOME/beepnado-seo.XXXXXXXX"')
trap 'ssh "$HOST" "rm -rf -- \"$STAGE\""' EXIT
find . -type f \( -name index.html -o -name sitemap.xml -o -name robots.txt -o -name seo-tools.js -o -name seo-tools.css \) -print0 | tar --null -T - -czf - | ssh "$HOST" "tar -xzf - -C \"$STAGE\""
ssh "$HOST" bash -s -- "$STAGE" <<'REMOTE'
set -Eeuo pipefail
stage="$1"
root="$HOME/portfolio/site/beepnado"
test -f "$root/index.html"
test -f "$stage/index.html"
backup="$HOME/beepnado-backups/seo-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp -a "$root/." "$backup/"
cp -a "$stage/." "$root/"
for path in / /resources/ /sitemap.xml /guides/monitor-linux-cron/ /solutions/wordpress/ /tools/ /tools/reliability-score/ /static/seo-tools.js /static/seo-tools.css; do
  curl -fsS --max-time 15 --resolve beepnado.com:443:127.0.0.1 "https://beepnado.com$path" >/dev/null || { echo "Verification failed for $path. Backup: $backup" >&2; exit 1; }
done
echo "Public SEO deployment verified. Backup: $backup"
REMOTE
