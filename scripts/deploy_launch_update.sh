#!/usr/bin/env bash
# Apply a reviewed release to the existing BeepNado deployment on yeet.
set -Eeuo pipefail
release="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app="$HOME/beepnado"
public="$HOME/portfolio/site/beepnado/index.html"
[[ -f "$app/compose.yaml" && -f "$public" ]] || { echo 'Expected BeepNado deployment missing.' >&2; exit 1; }
exec 9>"$app/.enable-signup.lock"
flock -n 9 || { echo 'Another BeepNado setup is active.' >&2; exit 1; }
cd "$app"

check_source() {
    local staged="$1" installed="$2" observed previous
    shift 2
    [[ -f "$staged" && -f "$installed" && ! -L "$installed" ]] ||
        { echo "Missing or unexpected file: $installed" >&2; return 1; }
    if cmp -s "$staged" "$installed"; then return 0; fi
    read -r observed _ < <(sha256sum -- "$installed")
    for previous; do
        if [[ "$observed" == "$previous" ]]; then return 0; fi
    done
    echo "Stopped to preserve local changes: $installed ($observed)" >&2
    return 1
}
check_source "$release/app/main.py" app/main.py 1629eff19d6d40a1b49ef6789ecb292d722b77b36b5b99a0e5ede18052a6ef0d 3f0a8f30928582c4034466d8c09a564ec151ca50a1b89d9df5b35a806684b4b0
check_source "$release/app/static/app.js" app/static/app.js 33e9f9d548438608f4b1f6d114c0eea8154819d358d139223741c6869dddf66c
check_source "$release/app/static/index.html" app/static/index.html b7077c86d1898177b15971cbd99887e52c70f0ebbd1230fd1d98f9b25bf4bec9 925e8ab80c610aadf0c63d473c88dd10b6fe7357f68a775575ba661072f5ecf8
check_source "$release/public/index.html" "$public" 654c5c94aff0471187622a486e5b52f064dce41c96117a04ea23aaa09f69522d 2b9f89334f9c408eef27672386e11e2932aeb7fa804574bea7576f6cc5bee52b

backup="$(mktemp -d "$HOME/beepnado-backups/launch.XXXXXXXX")"
cp -p app/main.py "$backup/main.py"
cp -p app/static/app.js "$backup/app.js"
cp -p app/static/index.html "$backup/app-index.html"
cp -p "$public" "$backup/public-index.html"

rollback() {
    trap - ERR INT TERM
    echo 'Restoring previous BeepNado files…' >&2
    cp -p "$backup/main.py" app/main.py
    cp -p "$backup/app.js" app/static/app.js
    cp -p "$backup/app-index.html" app/static/index.html
    cp -p "$backup/public-index.html" "$public"
    docker compose up -d --no-deps --build web || true
    echo "Backup: $backup" >&2
    exit 1
}
trap rollback ERR INT TERM

cp "$release/app/main.py" app/main.py
cp "$release/app/static/app.js" app/static/app.js
cp "$release/app/static/index.html" app/static/index.html
docker compose up -d --no-deps --build web

ready=false
for _ in {1..30}; do
    if curl -fsS --max-time 3 http://127.0.0.1:8490/health/ready >/dev/null 2>&1; then ready=true; break; fi
    sleep 2
done
[[ "$ready" == true ]]

fetch() {
    local host="$1" path="$2" file="$3"
    curl -fsS --max-time 10 --resolve "$host:443:127.0.0.1" "https://$host$path" -o "$file"
}
fetch app.beepnado.com / "$backup/served-app.html"
fetch app.beepnado.com /static/app.js "$backup/served-app.js"
cmp app/static/index.html "$backup/served-app.html"
cmp app/static/app.js "$backup/served-app.js"
cp "$release/public/index.html" "$public"
fetch beepnado.com / "$backup/served-public.html"
cmp "$public" "$backup/served-public.html"
fetch app.beepnado.com /api/availability "$backup/availability.json"
trap - ERR INT TERM
echo 'Pricing and checkout update verified through Caddy.'
cat "$backup/availability.json"
echo
echo "Backup: $backup"
