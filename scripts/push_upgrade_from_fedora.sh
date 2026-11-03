#!/usr/bin/env bash
# Run on Fedora inside a current checkout of cuzzaintit321/beepnado.
# Never uploads .env, SQLite files, or unrelated services.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${BEEPNADO_SSH_HOST:-yeet}"
ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/beepnado-update.XXXXXXXX.tar.gz")"
trap 'rm -f "$ARCHIVE"' EXIT
cd "$ROOT"
command -v ssh >/dev/null && command -v scp >/dev/null
[[ -f app/main.py && -f app/bootstrap_owner.py && -f app/mail_check.py && -f app/static/app.js && -f app/static/index.html ]] || {
  echo 'Required BeepNado release files are missing.' >&2; exit 1;
}
python3 -m py_compile app/main.py app/bootstrap_owner.py app/mail_check.py
if command -v node >/dev/null; then node --check app/static/app.js; fi
tar -czf "$ARCHIVE" app/main.py app/bootstrap_owner.py app/mail_check.py app/static/app.js app/static/index.html
scp "$ARCHIVE" "$HOST:~/beepnado-code-update.tar.gz"
ssh "$HOST" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
cd "$HOME/beepnado"
[[ -f compose.yaml && -f app/main.py && -f "$HOME/beepnado-code-update.tar.gz" ]] || {
  echo 'BeepNado or update archive not found on yeet.' >&2; exit 1;
}
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/beepnado-backups/code-$STAMP"
mkdir -p "$BACKUP/app/static"
cp -p app/main.py "$BACKUP/app/main.py"
cp -p app/static/app.js "$BACKUP/app/static/app.js"
cp -p app/static/index.html "$BACKUP/app/static/index.html"
if [[ -f app/bootstrap_owner.py ]]; then cp -p app/bootstrap_owner.py "$BACKUP/app/bootstrap_owner.py"; fi
if [[ -f app/mail_check.py ]]; then cp -p app/mail_check.py "$BACKUP/app/mail_check.py"; fi
# Use SQLite's live backup API instead of copying an open database file.
docker exec beepnado-web-1 python - "$STAMP" <<'PY'
import os,sqlite3,sys
from pathlib import Path
src=Path(os.getenv('DATABASE_PATH','/data/beepnado.sqlite3'))
if not src.exists():raise RuntimeError('Live database not found')
dst=src.parent/('beepnado-before-code-'+sys.argv[1]+'.sqlite3')
with sqlite3.connect(str(src)) as old,sqlite3.connect(str(dst)) as new: old.backup(new)
print('Database snapshot:',dst)
PY
restore(){
  echo 'Deploy failed. Restoring previous BeepNado code...' >&2
  cp -p "$BACKUP/app/main.py" app/main.py
  cp -p "$BACKUP/app/static/app.js" app/static/app.js
  cp -p "$BACKUP/app/static/index.html" app/static/index.html
  if [[ -f "$BACKUP/app/bootstrap_owner.py" ]]; then cp -p "$BACKUP/app/bootstrap_owner.py" app/bootstrap_owner.py; else rm -f app/bootstrap_owner.py; fi
  if [[ -f "$BACKUP/app/mail_check.py" ]]; then cp -p "$BACKUP/app/mail_check.py" app/mail_check.py; else rm -f app/mail_check.py; fi
  docker compose up -d --build || true
}
trap restore ERR
tar -xzf "$HOME/beepnado-code-update.tar.gz" -C "$HOME/beepnado"
python3 -m py_compile app/main.py app/bootstrap_owner.py app/mail_check.py
docker compose up -d --build
READY=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 http://127.0.0.1:8490/health/ready >/dev/null; then READY=1; break; fi
  sleep 2
done
[[ "$READY" == 1 ]] || { echo 'Readiness check failed.' >&2; exit 1; }
docker compose ps
echo 'BeepNado upgraded. Existing .env, database, Caddy and other sites were preserved.'
echo "Code backup: $BACKUP"
trap - ERR
REMOTE
