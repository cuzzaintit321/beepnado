<p align="center">
  <img src="assets/beepnado-cover.webp" alt="BeepNado — Your websites. Our radar. Uptime monitoring dashboard concept artwork" width="100%">
</p>

*Illustrative cover artwork. Uptime figures, monitored domains and incidents shown are example data.*

# BeepNado 🌪️ — Your websites. Our radar.

**BeepNado** is a self-hosted uptime monitoring platform for small businesses, freelancers, and web agencies. Built with Python, FastAPI, Docker, and SQLite, it tracks HTTP/HTTPS availability, response times, SSL certificate expiry, and outage/recovery history. Users can manage monitors in a secure dashboard and share public status pages. Email alerts require configured SMTP.

> **Launch status:** Beta release candidate. Public registration is disabled until email verification is configured; Stripe billing is not yet active.

## Incident Investigator (evidence-based)

BeepNado now offers an **Investigate** button on each monitor in the signed-in dark dashboard. It summarizes the latest recorded check and recent history, groups supported failure evidence into plain-English categories (DNS, TLS, timeout, connection refused, HTTP errors), suggests a next troubleshooting step, and lets the operator copy a client-ready evidence report. Results are **possible causes, not proven root causes**; only real check records are used.

The public landing page's generic feature grid and simulated downtime demonstration were removed, with a new Incident Investigator showcase in their place.

**Deploy both the application and the separate public landing page from Fedora:**

```bash
git -C ~/beepnado-launch pull --ff-only
bash ~/beepnado-launch/scripts/push_upgrade_from_fedora.sh
bash ~/beepnado-launch/scripts/deploy_public_investigator.sh
```

The second script updates only the public BeepNado marketing HTML and stylesheet, taking backups. Neither script configures live Stripe payments. Monitoring on the same server still cannot independently detect host-wide or Internet outages.

## Client pages and reports

In the workspace, open **Client pages**, give the page a title and slug, and select the monitors for that client. A new page starts as a draft. Enable **Publish this status page** and save to share the read-only link. Clear that option and save to take the link offline. Your account-wide status page is still available in **Status page**.

A saved client page can generate a 7-day or 30-day report, even while it is a draft. Reports include sampled pass rates, confirmed-down and uncertain observations, average response time for passing checks, and incident counts for the selected monitors. Copy the report or download it as text. Primary checks and heartbeat observations count toward the report. Check pass rates describe recorded samples rather than continuous availability.

## Dashboard outage alarm

Email protection is saved in your account and defaults to armed, preserving existing outage/recovery email behavior. The workspace has one **Arm / Disarm** button. Disarming stops queuing new incident emails (messages already queued may still be delivered); monitoring and incident history continue. Browser closure, logout and application restarts do not change the saved preference. Browser sound controls have been removed. SMTP must be configured; the server operator can verify delivery with `docker compose exec web python -m app.mail_check`. A primary server that loses power or internet access cannot send email; use an independent off-site worker to cover this failure mode.

## Public monitoring from a second network

Deploy the worker on a separate network so it can check public DNS, TLS, and HTTP even if the main BeepNado server is offline. On that host, copy `.worker.env.example` to `.worker.env` with mode 600. Set `WORKER_WATCH_URLS` to the HTTPS URLs you operate, then start the worker:

```bash
install -m 600 .worker.env.example .worker.env
# Edit .worker.env before starting the worker.
docker compose -f compose.worker.yaml up -d --build
docker compose -f compose.worker.yaml logs --tail=30 worker
```

With `WORKER_ALERT_EMAIL` and TLS SMTP configured on the worker, two consecutive failed public checks queue one outage email. A successful check after a confirmed outage queues one recovery email. The worker stores alert state and retries in its own volume. These operator alerts run independently of dashboard maintenance windows.

To see public observations beside primary checks, set `BEEPNADO_URL` and a random `WORKER_TOKEN` in `.worker.env`. Set the same token on the main server and recreate its web container. The token grants access to active website target URLs, so give it only to a trusted worker. The public probes measure ordinary HTTP(S) availability. Your primary check rules and incident state are tracked separately. The dashboard marks public samples over 15 minutes old as stale and displays observations from the past hour.

## Website and network monitors

The Add monitor menu supports website and heartbeat monitors and these public checks:

- **JSON API** sends a GET request and validates the expected HTTP status and JSON. An optional JSON Pointer can verify a scalar value.
- **DNS record** checks A, AAAA, CNAME, MX, NS, TXT or CAA through Google Public DNS, with an optional expected answer.
- **SSL certificate** validates the hostname and certificate chain and checks the expiry threshold once an hour.
- **Domain expiry** checks registration expiry through the registry's HTTPS RDAP service every six hours. If the registry does not provide an expiry date, the check is inconclusive.
- **ICMP ping**, **TCP port** and **UDP reply** check public hosts. UDP requires an actual reply to a request of 1–128 bytes and can match a reply prefix.

The Compose files permit ICMP datagram sockets for the BeepNado group without granting raw-socket capability. New workers can report the network types to the dashboard. Existing workers continue to receive website monitors.

For an ambiguous network failure, the primary or remote probe checks public HTTPS connectivity to two independent reference hosts. If neither answers, the failed probe is inconclusive and cannot open or close an incident. An off-site worker and its independent alerting can detect when a site on the primary server's internet connection becomes unreachable.

## Page journeys

In **Monitors**, choose **Page journey**. Enter the public site origin and add two to five paths, each with an expected HTTP status and optional required text. A journey counts as one monitor. It makes GET requests in order and stops at the first failed or inconclusive step. Use **Edit journey** to change the steps and keep historical evidence.

**Investigate** shows the attempted steps, their status codes and timings, and the first failure. Confirmed outages use the existing alarm and alert flow. Journey checks cover public HTTP responses. They do not submit forms, authenticate, or run page scripts. For a second vantage point, update the off-site worker to this release. Workers on earlier releases continue receiving the monitor types they already support.

## Planned maintenance

Open **Maintenance** on a website monitor to start a window now or schedule one in your local time. The checks keep running at the usual interval, and the dashboard marks those results in the check history. During the window BeepNado suppresses outage and recovery alerts for that monitor and excludes the planned checks from its success rate. A public status page shows the upcoming or active window. End it early from the same dialog to resume normal alerting at the next check.

## Heartbeat monitors

To monitor a cron job, backup, or scheduled task, open **Monitors**, choose **Heartbeat**, and set the expected interval and grace period. Copy the private check-in command and run it only after the job succeeds. BeepNado opens an incident when the interval plus grace period elapses and resolves it when the next check-in arrives. The alert feed records both events and configured SMTP sends email to verified accounts.

Website and heartbeat monitors share the account's monitor limit. The command is shown when the monitor is created. **Replace key** generates a fresh command and invalidates the old one immediately.

## Dark monitoring workspace (v3)

BeepNado's signed-in monitoring UI now uses a compact dark operations layout inspired by modern uptime dashboards while preserving its own branding. It includes searchable/filterable monitors, recent check tick bars **only where actual check records exist**, up/down/pending totals, 30-day check-success statistics, manual checks, monitor creation, and access to incident history and status pages.

**Update an existing deployment** (from Fedora with SSH to `yeet`):

```bash
git -C ~/beepnado-launch pull --ff-only
bash ~/beepnado-launch/scripts/push_upgrade_from_fedora.sh
```

The UI update uses `app/static/monitor-theme.css` and `app/static/monitor-view.js`. It does not change the SQLite schema, `.env` secrets, the public marketing website, or the Caddy configuration. The self-host monitoring workaround for domains on `yeet` still requires the separate `scripts/fix_local_monitor.sh` configuration when applicable. Same-host checks cannot independently detect whole-server or home-Internet outages.

## Get your own monitoring dashboard working now

The deployed BeepNado app is reachable at **https://app.beepnado.com**. Public registration remains closed until verified SMTP is set up, but the server operator can activate an owner account without opening public registration.

On **Fedora xps-8930** (requires authenticated GitHub CLI and SSH access to `yeet`):

```bash
gh repo clone cuzzaintit321/beepnado ~/beepnado-launch
cd ~/beepnado-launch
bash scripts/push_upgrade_from_fedora.sh
```

This targeted update snapshots the persistent SQLite database, backs up the app files, rebuilds **only BeepNado**, and checks `/health/ready`. It does **not** replace `.env`, delete the Docker volume, or reconfigure Caddy/your other websites.

Then create your own verified Free account locally (password is entered interactively, never on the command line):

```bash
ssh -t yeet 'cd ~/beepnado && docker compose exec web python -m app.bootstrap_owner'
```

Sign in at **https://app.beepnado.com/?signin=1**, add a public URL you own/are authorized to monitor, click **Check**, and create a public status page. Existing email accounts remain intact; the bootstrap command refuses to overwrite an already-verified account. The dashboard shows check-based 30-day success percentages **only when real samples exist**, not fabricated uptime or an SLA.

### Open limited public beta registration (after email is tested)

In `~/beepnado/.env` on **yeet**, configure SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM and the appropriate TLS options. Keep `REGISTRATION_ENABLED=false` until you verify delivery:

```bash
cd ~/beepnado
docker compose up -d --force-recreate
docker compose exec web python -m app.mail_check
```

After the test email arrives and the full verification/password-recovery loop passes, change `REGISTRATION_ENABLED=true`, rebuild or recreate the app, and test a fresh account. Never share the SMTP password in a chat or commit it to GitHub.

**Paid plans remain unavailable:** Stripe live payments, webhook events, cancellation/upgrade flows, tax obligations, a tested off-site monitor, and independent security review are still outstanding. Do not advertise 24/7 alert guarantees on a single home server.

## What's implemented and tested

- Existing HTTP(S) monitoring, HTTPS certificate expiry visibility, incident and recovery records, in-app notifications and shareable status pages.
- Independent user sessions with secure cookies, scrypt password hashing, token-based email verification, password reset with session invalidation, CSRF guard on authenticated writes, basic registration/login throttles and a configurable public beta account cap (default 50).
- Queued SMTP outage/recovery email alerts with retries (only for verified accounts when SMTP is configured), separate from customer dashboard notifications.
- Functional free plan (2 monitors, 5-minute checks), subject to verification email being configured and registration being enabled.
- Public website feature/price detail dialogs, realistic but **explicitly simulated** uptime/down/recovery demo, FAQ and responsive keyboard interactions.
- Stripe hooks remain opt-in and unconfigured; a paid tier is **not** available until full test subscriptions, webhook security, subscription cancellation, receipts and live billing are validated.

## Deploy to yeet (preserves previous data)

This package does not itself connect to your server. On Fedora, download `beepnado-final-release.tar.gz`, then:

```bash
scp ~/Downloads/beepnado-final-release.tar.gz yeet:~/
ssh yeet 'mkdir -p ~/beepnado-final-release && tar -xzf ~/beepnado-final-release.tar.gz -C ~/beepnado-final-release --strip-components=1 && bash ~/beepnado-final-release/scripts/deploy_yeet.sh'
```

The installer:
1. Checks the existing `~/beepnado` source, `~/portfolio/Caddyfile`, public BeepNado site and `portfolio_default` Docker network.
2. Creates source, Caddyfile and static-site backups under `~/beepnado-backups/`; snapshots the **existing SQLite database** inside the persistent Docker volume using SQLite's online backup API.
3. Copies the upgraded app and Compose files, preserves `.env` credentials and data volumes, sets HTTPS session cookies and the `https://app.beepnado.com` site URL, and attaches the app to Caddy's shared Docker network.
4. Rebuilds the existing `beepnado-web-1`, checks local HTTP health and Caddy-to-app connectivity, and only then adds `app.beepnado.com` to Caddy.
5. Adds interactive public-site JS and CSS and makes the Free-tier CTA link to the customer workspace.
6. Validates and reloads Caddy. It has rollback of source files and Caddy config on installer errors; the SQLite snapshot is retained separately in the Docker volume.

**Do not run** `docker compose down -v`, which would delete monitoring records.

### Point the app subdomain to yeet

IONOS → Domains & SSL → beepnado.com → DNS → Add record:

| Type | Host | Points to |
| --- | --- | --- |
| CNAME | `app` | `beepnado.com` |

Keep `@`, `www`, MX, TXT, and email records as they are. Since your home router lacks reliable NAT hairpinning, add this line on each Fedora PC if necessary:

```text
192.168.1.130 app.beepnado.com
```

Check app HTTPS **from `yeet`** independent of router loopback:

```bash
curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/
```

The public marketing site stays at `https://beepnado.com` and the account app will be at `https://app.beepnado.com` once DNS is propagated. Both use Caddy automatic HTTPS.

### Enable free public signup — requires working SMTP

New registrations are deliberately disabled until verification and recovery email can be delivered reliably. Edit `~/beepnado/.env` **on yeet**, without sharing secrets in chat:

```dotenv
SITE_URL=https://app.beepnado.com
SESSION_SECURE=true
REGISTRATION_ENABLED=false
SMTP_HOST=YOUR_SMTP_SERVER
SMTP_PORT=587
SMTP_USER=YOUR_MAILBOX_USER
SMTP_PASSWORD=YOUR_MAILBOX_APP_PASSWORD
SMTP_FROM=YOUR_VERIFIED_SENDER
SMTP_STARTTLS=true
SMTP_SSL=false
```

Create a real SMTP mailbox/provider and test sending *before* enabling registration. Example private SMTP test (replace the recipient address on your own computer):

```bash
cd ~/beepnado
docker compose up -d
docker compose exec -T web python -c 'from app.main import send_email;send_email("YOUR_EMAIL", "BeepNado SMTP test", "Email verification can be delivered.")'
```

Check actual inbox receipt; then set `REGISTRATION_ENABLED=true` in `.env` and run `docker compose up -d --force-recreate`. Verify a real signup, verification link, monitor creation, incident history, password reset email and logout on the public HTTPS app before inviting customers. Do not use test/private data in public status-page names. Existing accounts created under the old unverified prototype must verify their email; they are not silently trusted by the migration.

### Diagnostics

```bash
cd ~/beepnado
docker compose ps
docker compose logs --tail=80 web
curl -fsS http://127.0.0.1:8490/health
curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/
docker logs jason-portfolio --tail 60
```

### Unfinished before a paid commercial launch

- Correctly configured and tested Stripe subscriptions and webhook integration; live purchase/cancel/upgrade flows; taxes, billing terms, and chargeback/support processes.
- Independent security review, account abuse testing, enhanced logging/monitoring, external regions, real outbound email operation, domain reputation, offsite backup restore drill, privacy policy/terms, support contact, and uptime/reliability commitments.
- This is a **beta release candidate** rather than an audited commercial SaaS. Avoid claims of 24/7 staffing, worldwide monitoring, or SLA guarantees.

## Tests

```bash
python -m pip install -r requirements.txt httpx
SESSION_SECURE=false python -m unittest discover -s tests -v
node --check app/static/app.js
node --check app/static/experience.js
```

The development environment ran **18 backend tests** successfully; the interactive marketing layer passed desktop/mobile browser DOM tests. A Docker image was *not* built and the installer has not been executed against the live `yeet` server from this chat.
