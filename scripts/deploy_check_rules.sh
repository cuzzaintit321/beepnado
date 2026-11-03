#!/usr/bin/env bash
# Apply the monitor rules release on yeet and verify it through the existing proxy.
set -Eeuo pipefail
release="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app="$HOME/beepnado"
state="$HOME/.local/state/beepnado"
[[ -f "$app/compose.yaml" && -f "$app/.env" ]] || { echo 'BeepNado installation missing.' >&2; exit 1; }
umask 077
mkdir -p "$state"
exec 9>"$app/.enable-signup.lock"
flock -n 9 || { echo 'Another BeepNado setup is running.' >&2; exit 1; }
cd "$app"

expect_release_file() {
    local file="$1" observed expected
    shift
    if cmp -s "$file" "$release/$file"; then return; fi
    read -r observed _ < <(sha256sum -- "$file")
    for expected; do
        if [[ "$observed" == "$expected" ]]; then return; fi
    done
    echo "Stopped to preserve local changes in $file ($observed)." >&2
    return 1
}

expect_release_file app/main.py 463d79e4917dab6a5874dc9a56081a997186bf3a486204f8a73475047bd62c3c 0e73bcd368b147c4016f19ae3a50eb0ee9b147b0cc8e6d11a13c824ca2d835b1
expect_release_file app/static/monitor-view.js d7569b521cb79c9bea42afdfb0f043c97b8f0fdcdda7f76c53e65a8518af72b6 a2997738a91b875bccf9709ff7638e5ed510b9947bbc44a3f4aebd4e5a65deae
expect_release_file requirements.txt c7b3d34378d337125cc58d87dd542b70322cffac63339196e5c23cdfeb9c9cef

staged_main="$state/release/app/main.py"
if [[ -f "$staged_main" ]] && ! cmp -s "$staged_main" "$release/app/main.py"; then
    printf '%s  %s\n' 0e73bcd368b147c4016f19ae3a50eb0ee9b147b0cc8e6d11a13c824ca2d835b1 "$staged_main" |
        sha256sum --check --status || { echo 'Existing paid release was modified. Review before updating.' >&2; exit 1; }
fi

backup="$(mktemp -d "$state/rules-backup.XXXXXXXX")"
files=(app/main.py app/static/monitor-view.js app/static/monitor-theme.css requirements.txt)
cp -p --parents -- "${files[@]}" "$backup"
if [[ -f "$staged_main" ]]; then
    cp -p -- "$staged_main" "$backup/staged-main.py"
fi

caddy() {
    curl -fsS --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 "https://app.beepnado.com$1"
}
caddy /api/availability >"$backup/availability.json"
docker compose exec -T web python -c '
from contextlib import closing
from pathlib import Path
import sqlite3, sys, tempfile
from app.main import DB_PATH
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "backup.sqlite3"
    with closing(sqlite3.connect(DB_PATH)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SystemExit("Database backup check failed")
    with path.open("rb") as data:
        import shutil
        shutil.copyfileobj(data, sys.stdout.buffer)
' >"$backup/before-rules.sqlite3"

rollback() {
    trap - ERR INT TERM
    echo "Restore from $backup" >&2
    for file in "${files[@]}"; do cp -p -- "$backup/$file" "$file"; done
    if [[ -f "$backup/staged-main.py" ]]; then cp -p -- "$backup/staged-main.py" "$staged_main"; fi
    docker compose up -d --no-deps --build web || true
    exit 1
}
trap rollback ERR INT TERM
cp -- "$release/app/main.py" app/main.py
cp -- "$release/app/static/monitor-view.js" app/static/monitor-view.js
cp -- "$release/requirements.txt" requirements.txt
# Append only the new rules to preserve local dashboard styling.
marker='/* Configurable check rules */'
if ! grep -Fxq "$marker" app/static/monitor-theme.css; then
    printf '\n' >> app/static/monitor-theme.css
    sed -n '/^\/\* Configurable check rules \*\/$/,$p' "$release/app/static/monitor-theme.css" >> app/static/monitor-theme.css
fi
grep -Fxq "$marker" app/static/monitor-theme.css
docker compose up -d --build --no-deps --wait --wait-timeout 90 web
caddy /health/ready >/dev/null
caddy /static/monitor-view.js >"$backup/served-monitor-view.js"
cmp app/static/monitor-view.js "$backup/served-monitor-view.js"
caddy /static/monitor-theme.css >"$backup/served-monitor-theme.css"
cmp app/static/monitor-theme.css "$backup/served-monitor-theme.css"
caddy /api/availability >"$backup/after-availability.json"
cmp "$backup/availability.json" "$backup/after-availability.json"
docker compose exec -T web python -c '
from app.main import db
with db() as c:
    c.execute("SELECT required_phrase, expected_status, max_response_ms FROM monitors LIMIT 1")
'
if [[ -f "$staged_main" ]]; then
    cp -- "$release/app/main.py" "$staged_main"
fi
trap - ERR INT TERM
echo 'Check rules deployed and verified through Caddy.'
echo "Backup: $backup"
