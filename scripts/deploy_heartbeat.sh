#!/usr/bin/env bash
# Apply the release on yeet. Pass --public-only to publish marketing files without a restart.
set -Eeuo pipefail
release="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app="$HOME/beepnado"
state="$HOME/.local/state/beepnado"
public_root="$HOME/portfolio/site/beepnado"
public_page="$public_root/index.html"
public_assets=(static/favicon-96.png static/guide.css static/monitor-types.css guides/check-website-downtime/index.html
    website-monitoring-for-agencies/index.html robots.txt sitemap.xml)
[[ -d "$app" && -f "$public_page" ]] || { echo 'BeepNado site paths missing.' >&2; exit 1; }
umask 077
mkdir -p "$state"
exec 9>"$app/.enable-signup.lock"
flock -n 9 || { echo 'Another BeepNado setup is running.' >&2; exit 1; }

public_caddy() {
    curl -fsS --max-time 12 --resolve beepnado.com:443:127.0.0.1 "https://beepnado.com$1"
}

publish_public() {
    local source="$1" target="$2" temporary
    temporary="$(mktemp "$(dirname -- "$target")/.beepnado.XXXXXXXX")"
    cp -p -- "$source" "$temporary"
    mv -f -- "$temporary" "$target"
}

backup_public_files() {
    cp -p -- "$public_page" "$backup/public-before.html"
    mkdir "$backup/public-assets"
    for file in "${public_assets[@]}"; do
        [[ -f "$release/public/$file" ]]
        if [[ -e "$public_root/$file" ]]; then
            [[ -f "$public_root/$file" ]] || { echo "Unexpected path $public_root/$file" >&2; exit 1; }
            (cd "$public_root" && cp -p --parents -- "$file" "$backup/public-assets")
        fi
    done
}

restore_public_files() {
    if ! cmp -s "$backup/public-before.html" "$public_page"; then
        publish_public "$backup/public-before.html" "$public_page"
    fi
    for file in "${public_assets[@]}"; do
        if [[ -f "$backup/public-assets/$file" ]]; then
            publish_public "$backup/public-assets/$file" "$public_root/$file"
        else
            rm -f -- "$public_root/$file"
        fi
    done
}

publish_public_files() {
    for file in "${public_assets[@]}"; do
        (umask 022; mkdir -p -- "$(dirname -- "$public_root/$file")")
        publish_public "$release/public/$file" "$public_root/$file"
    done
    if ! cmp -s "$backup/public-after.html" "$public_page"; then
        chmod --reference="$backup/public-before.html" "$backup/public-after.html"
        publish_public "$backup/public-after.html" "$public_page"
    fi
}

verify_public_files() {
    public_caddy / >"$backup/served-public.html"
    cmp "$public_page" "$backup/served-public.html"
    for file in "${public_assets[@]}"; do
        public_caddy "/${file%index.html}" >"$backup/served-seo-asset"
        cmp "$release/public/$file" "$backup/served-seo-asset"
    done
}

if [[ "${1:-}" == --public-only && $# == 1 ]]; then
    backup="$(mktemp -d "$state/marketing-backup.XXXXXXXX")"
    backup_public_files
    python3 "$release/scripts/stage_entry_pages.py" --public-only "$release" \
        "$backup/public-before.html" "$backup/public-after.html"
    rollback_public() {
        trap - ERR INT TERM
        echo "Restore from $backup" >&2
        restore_public_files
        exit 1
    }
    trap rollback_public ERR INT TERM
    publish_public_files
    verify_public_files
    trap - ERR INT TERM
    echo 'Public SEO files deployed and verified through Caddy.'
    echo "Backup: $backup"
    exit
fi
if (( $# != 0 )); then
    echo 'Usage: deploy_heartbeat.sh [--public-only]' >&2
    exit 2
fi
[[ -f "$app/compose.yaml" && -f "$app/.env" ]] || { echo 'BeepNado application files missing.' >&2; exit 1; }
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

expect_release_file app/main.py 7d1ad4840f081ebddf538c60f0b3b3ff55e2ce232e099b4d24baf906d1bbcbc2 b444cf0943d58e464518daf20248614237c0cd5d86d3f19ad151b43607a698c1 1556f3cdee407dee2ce939a4c081a972ca5b042f2fce5ec8888e4f8c3a1c52d9 463d79e4917dab6a5874dc9a56081a997186bf3a486204f8a73475047bd62c3c 0e73bcd368b147c4016f19ae3a50eb0ee9b147b0cc8e6d11a13c824ca2d835b1 b0c407c8ec8c0687f55c8e3c1062d47fee53518017ca55884d4ae31c5e6b551e 8f96fdf69ddc440bcb0b716fc6a0e3533ff97813a4c2405c2693303440b29676 eee66058746487fb8cff8e507966bf0ce0a080c84eca239aba3648024ee5e370 6eed33a5ba88ed3f848c3766df539ffe6fabd3e3bbd2c7087b28026d78e36ed5 7deb7b032a1862afd200f69328ea204ebca3bea323861f3f0829fb02f8c9f62b
expect_release_file app/static/monitor-view.js 539c2135c6f821169bf5fccdf42a160cbdcf2b2830b414b8b521c1815dc1c1a5 d426c4763c335060b9594c10e9dcc67c820ff47c87d6614f09888f804f33026a d7569b521cb79c9bea42afdfb0f043c97b8f0fdcdda7f76c53e65a8518af72b6 a2997738a91b875bccf9709ff7638e5ed510b9947bbc44a3f4aebd4e5a65deae 4a165fd2c214fa63496f7fa6721b5bd1a00ccb65de529c50d234ea23f5e8f00e 4fcefd3fddef0046279c0733450466b0377f5553d679eb9d9f57c5c7b588014f 97deadce2c8cb6be49432d8f77d803b9ba98a7b1736e99505c28b8ac0818c5f5 b3a34f0bc39bae085f292d87f347efa25967bd13ff6804302668672e04496e30
expect_release_file requirements.txt c7b3d34378d337125cc58d87dd542b70322cffac63339196e5c23cdfeb9c9cef ab59b5f98e5e75669346e0f877691470741461658fdaee164162ac885ebc8be4

staged_main="$state/release/app/main.py"
if [[ -f "$staged_main" ]] && ! cmp -s "$staged_main" "$release/app/main.py"; then
    read -r staged_digest _ < <(sha256sum -- "$staged_main")
    case "$staged_digest" in
        7d1ad4840f081ebddf538c60f0b3b3ff55e2ce232e099b4d24baf906d1bbcbc2|b444cf0943d58e464518daf20248614237c0cd5d86d3f19ad151b43607a698c1|1556f3cdee407dee2ce939a4c081a972ca5b042f2fce5ec8888e4f8c3a1c52d9|0e73bcd368b147c4016f19ae3a50eb0ee9b147b0cc8e6d11a13c824ca2d835b1|b0c407c8ec8c0687f55c8e3c1062d47fee53518017ca55884d4ae31c5e6b551e|8f96fdf69ddc440bcb0b716fc6a0e3533ff97813a4c2405c2693303440b29676|eee66058746487fb8cff8e507966bf0ce0a080c84eca239aba3648024ee5e370|6eed33a5ba88ed3f848c3766df539ffe6fabd3e3bbd2c7087b28026d78e36ed5|7deb7b032a1862afd200f69328ea204ebca3bea323861f3f0829fb02f8c9f62b) ;;
        *) echo 'Existing paid release was modified. Review before updating.' >&2; exit 1 ;;
    esac
fi

backup="$(mktemp -d "$state/monitoring-backup.XXXXXXXX")"
files=(app/main.py app/static/index.html app/static/app.js app/static/monitor-view.js app/static/monitor-theme.css requirements.txt compose.yaml Dockerfile)
cp -p --parents -- "${files[@]}" "$backup"
optional_files=(app/network_checks.py app/journeys.py app/static/client-pages.js app/static/outage-alarm.js)
for file in "${optional_files[@]}"; do
    if [[ -f "$file" ]]; then cp -p --parents -- "$file" "$backup"; fi
done
backup_public_files
# Patch only the status rendering in app.js, preserving locally deployed billing and signup behavior.
patch_file="$backup/public-status.patch"
cat >"$patch_file" <<'PUBLIC_STATUS_PATCH'
--- a/app/static/app.js
+++ b/app/static/app.js
@@ -6,3 +6,3 @@
 const dateOnly = value => value ? new Date(value).toLocaleDateString() : '—';
-const prettyState = s => s==='up'?'Operational':s==='down'?'Down':s==='degraded'?'Unconfirmed failure':'Awaiting first check';
+const prettyState = s => s==='up'?'Operational':s==='down'?'Down':s==='degraded'?'Unconfirmed failure':s==='maintenance'?'Maintenance':'Awaiting check';
 const pill = state => `<span class="status-pill status-${esc(state)}">● ${prettyState(state)}</span>`;
@@ -151,7 +151,20 @@
 async function renderPublicStatus(slug){
- show('status');
  try{
    const s=await api('/api/public-status/'+encodeURIComponent(slug));
-   const bad=s.monitors.some(m=>m.state==='down');const pending=s.monitors.some(m=>m.state!=='up');
-   $('status-body').innerHTML=`<span class="kicker">SERVICE HEALTH</span><h1>${esc(s.title)}</h1><p>Last loaded: ${fmt(s.observed_at)}</p><div class="status-banner ${bad?'alert':''}">${bad?'● One or more services are currently reporting downtime':pending?'◌ Some services have not confirmed a healthy state':'✓ All monitored services are operational'}</div><div class="panel"><h3>Services</h3>${s.monitors.length?`<div class="table-wrap"><table><thead><tr><th>Service</th><th>Status</th><th>Last checked</th></tr></thead><tbody>${s.monitors.map(m=>`<tr><td><strong>${esc(m.name)}</strong></td><td>${pill(m.state)}</td><td>${fmt(m.last_checked)}</td></tr>`).join('')}</tbody></table></div>`:empty('No public services listed','The account has not added a monitor.')}</div><div class="panel"><h3>Recent incidents</h3>${s.incidents.length?`<div class="table-wrap"><table><thead><tr><th>Service</th><th>Opened</th><th>Recovered</th></tr></thead><tbody>${s.incidents.map(i=>`<tr><td>${esc(i.monitor)}</td><td>${fmt(i.opened_at)}</td><td>${i.resolved_at?fmt(i.resolved_at):'Ongoing'}</td></tr>`).join('')}</tbody></table></div>`:empty('No incidents recorded','This page only displays incidents already observed by the monitor.')}</div><p class="billing-note">Displayed values reflect the most recent primary-region HTTP(S) observations, not a guaranteed SLA.</p>`;
+   const bad=s.monitors.some(m=>m.state==='down');
+   const maintenance=s.monitors.some(m=>m.state==='maintenance');
+   const pending=s.monitors.some(m=>!['up','maintenance'].includes(m.state));
+   const message=bad?'● One or more services are currently reporting downtime'
+     :maintenance?'◌ Planned maintenance is in progress'
+     :pending?'◌ Some services have not confirmed a healthy state':'✓ All monitored services are operational';
+   const rows = s.monitors.map(m => {
+     const window = m.maintenance;
+     const schedule = window && (m.state === 'maintenance'
+       ? 'Ends ' + fmt(window.ends_at)
+       : 'Scheduled ' + fmt(window.starts_at) + ' to ' + fmt(window.ends_at));
+     return `<tr><td><strong>${esc(m.name)}</strong></td>
+       <td>${pill(m.state)}${schedule ? '<div class="small-muted">' + esc(schedule) + '</div>' : ''}</td>
+       <td>${fmt(m.last_checked)}</td></tr>`;
+   }).join('');
+   $('status-body').innerHTML=`<span class="kicker">SERVICE HEALTH</span><h1>${esc(s.title)}</h1><p>Last loaded: ${fmt(s.observed_at)}</p><div class="status-banner ${bad?'alert':''}">${message}</div><div class="panel"><h3>Services</h3>${s.monitors.length?`<div class="table-wrap"><table><thead><tr><th>Service</th><th>Status</th><th>Last checked</th></tr></thead><tbody>${rows}</tbody></table></div>`:empty('No public services listed','The account has not added a monitor.')}</div><div class="panel"><h3>Recent incidents</h3>${s.incidents.length?`<div class="table-wrap"><table><thead><tr><th>Service</th><th>Opened</th><th>Recovered</th></tr></thead><tbody>${s.incidents.map(i=>`<tr><td>${esc(i.monitor)}</td><td>${fmt(i.opened_at)}</td><td>${i.resolved_at?fmt(i.resolved_at):'Ongoing'}</td></tr>`).join('')}</tbody></table></div>`:empty('No incidents recorded','This page only displays incidents already observed by the monitor.')}</div><p class="billing-note">Displayed values reflect the latest recorded monitor observations.</p>`;
  }catch(e){$('status-body').innerHTML=`<h1>Status page unavailable</h1><p>${esc(e.message)}</p>`}
@@ -160,3 +173,8 @@
  const statusMatch=location.pathname.match(/^\/s\/([a-z0-9-]+)$/);
- if(statusMatch){await renderPublicStatus(statusMatch[1]);return}
+ if(statusMatch){
+   show('status');
+   await renderPublicStatus(statusMatch[1]);
+   setInterval(()=>{if(!document.hidden)renderPublicStatus(statusMatch[1]);},30000);
+   return;
+ }
  try{launchCapabilities=await api('/api/availability')}catch(e){console.warn('Launch status unavailable')}
PUBLIC_STATUS_PATCH
# Prepare both page changes before replacing any deployed file.
ui="$backup/ui"
mkdir -p "$ui"
cp -p --parents -- app/static/app.js app/static/index.html "$ui"
# Stage the two narrow runtime changes against the installed files.
python3 - "$backup" <<'PING_RUNTIME'
from pathlib import Path
import sys
root = Path(sys.argv[1])
edits = {
    'compose.yaml': ('    cap_drop: [ALL]', '    cap_drop: [ALL]\n    sysctls:\n      net.ipv4.ping_group_range: "10002 10002"'),
    'Dockerfile': ('&& useradd -u 10002 -m', '&& groupadd -g 10002 beepnado && useradd -u 10002 -g 10002 -m'),
}
for filename, (old, new) in edits.items():
    source = (root / filename).read_text()
    if new not in source:
        if source.count(old) != 1 or (filename == 'compose.yaml' and 'sysctls:' in source):
            raise SystemExit(f'Review local {filename} before enabling ICMP')
        source = source.replace(old, new, 1)
    (root / 'ui' / filename).write_text(source)
PING_RUNTIME
auth_patch="$release/scripts/app_workspace.patch"
incident_patch="$release/scripts/app_incidents.patch"
client_patch="$release/scripts/app_client_pages.patch"
(
    cd "$ui"
    if ! git apply --reverse --check "$auth_patch" 2>/dev/null; then
        if ! git apply --check "$auth_patch" 2>/dev/null; then
            git apply "$patch_file"
        fi
        git apply "$auth_patch"
    fi
    for patch in "$incident_patch" "$client_patch"; do
        if ! git apply --reverse --check "$patch" 2>/dev/null; then
            git apply "$patch"
        fi
    done
)
python3 "$release/scripts/stage_entry_pages.py" "$release" "$ui/app/static/index.html" \
    "$backup/public-before.html" "$backup/public-after.html"
if [[ -f "$staged_main" ]]; then
    cp -p -- "$staged_main" "$backup/staged-main.py"
fi

caddy() {
    local path="$1"
    shift
    curl -fsS --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 "$@" "https://app.beepnado.com$path"
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
' >"$backup/before-monitoring.sqlite3"

rollback() {
    trap - ERR INT TERM
    echo "Restore from $backup" >&2
    for file in "${files[@]}"; do cp -p -- "$backup/$file" "$file"; done
    for file in "${optional_files[@]}"; do
        if [[ -f "$backup/$file" ]]; then
            cp -p -- "$backup/$file" "$file"
        else
            rm -f -- "$file"
        fi
    done
    restore_public_files
    if [[ -f "$backup/staged-main.py" ]]; then cp -p -- "$backup/staged-main.py" "$staged_main"; fi
    docker compose up -d --no-deps --build web || true
    exit 1
}
trap rollback ERR INT TERM
cp -- "$ui/app/static/app.js" app/static/app.js
cp -- "$ui/app/static/index.html" app/static/index.html
cp -- "$release/app/main.py" app/main.py
for file in "${optional_files[@]}"; do cp -- "$release/$file" "$file"; done
cp -- "$ui/compose.yaml" compose.yaml
cp -- "$ui/Dockerfile" Dockerfile
docker compose config -q
cp -- "$release/app/static/monitor-view.js" app/static/monitor-view.js
cp -- "$release/requirements.txt" requirements.txt
# Preserve local styling by appending each missing section.
for marker in '/* Configurable check rules */' '/* Heartbeat monitors */' '/* Planned maintenance */' '/* Public probe evidence */' '/* Workspace entry */' '/* Network monitor evidence */' '/* Client pages and monitoring reports */' '/* Outage alarm */' '/* Page journeys */'; do
    if ! grep -Fxq "$marker" app/static/monitor-theme.css; then
        printf '\n' >> app/static/monitor-theme.css
        awk -v marker="$marker" '
            $0 == marker { active = 1 }
            active && $0 != marker && /^\/\*/ { exit }
            active { print }
        ' "$release/app/static/monitor-theme.css" >> app/static/monitor-theme.css
    fi
    grep -Fxq "$marker" app/static/monitor-theme.css
done
docker compose up -d --build --no-deps --wait --wait-timeout 90 web
caddy /health/ready >/dev/null
caddy / -D "$backup/workspace-headers.txt" >"$backup/served-workspace.html"
grep -Eiq '^x-robots-tag:[[:space:]]*noindex[[:space:]]*$' "$backup/workspace-headers.txt"
cmp app/static/index.html "$backup/served-workspace.html"
status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 -X POST https://app.beepnado.com/api/heartbeat)"
[[ "$status" == 401 ]]
caddy /static/app.js >"$backup/served-app.js"
cmp app/static/app.js "$backup/served-app.js"
caddy /static/monitor-view.js >"$backup/served-monitor-view.js"
cmp app/static/monitor-view.js "$backup/served-monitor-view.js"
caddy /static/client-pages.js >"$backup/served-client-pages.js"
cmp app/static/client-pages.js "$backup/served-client-pages.js"
caddy /static/outage-alarm.js >"$backup/served-outage-alarm.js"
cmp app/static/outage-alarm.js "$backup/served-outage-alarm.js"
status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/api/client-pages)"
[[ "$status" == 401 ]]
status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/api/alarm-incidents)"
[[ "$status" == 401 ]]
caddy /static/monitor-theme.css >"$backup/served-monitor-theme.css"
cmp app/static/monitor-theme.css "$backup/served-monitor-theme.css"
status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 12 --resolve app.beepnado.com:443:127.0.0.1 -X POST https://app.beepnado.com/api/monitors/journey)"
[[ "$status" == 401 ]]
caddy /api/availability >"$backup/after-availability.json"
cmp "$backup/availability.json" "$backup/after-availability.json"
docker compose exec -T web python -c '
import socket
from app.main import db
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP):
    pass
with db() as c:
    c.execute("SELECT required_phrase, expected_status, max_response_ms, heartbeat_due_at, maintenance_last_checked FROM monitors LIMIT 1")
    c.execute("SELECT id FROM maintenance_windows LIMIT 1")
    c.execute("SELECT probe_kind, probe_config, detail FROM monitors LIMIT 1")
    c.execute("SELECT opening_evidence, recovery_evidence FROM incidents LIMIT 1")
    c.execute("SELECT journey_trace FROM checks LIMIT 1")
    c.execute("SELECT id, published FROM client_pages LIMIT 1")
    c.execute("SELECT page_id, monitor_id FROM client_page_monitors LIMIT 1")
'
publish_public_files
verify_public_files
if [[ -f "$staged_main" ]]; then
    cp -- "$release/app/main.py" "$staged_main"
fi
trap - ERR INT TERM
echo 'Marketing SEO, workspace entry, monitoring, client pages, outage alarm, and page journeys deployed and verified through Caddy.'
echo "Backup: $backup"
