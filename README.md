<p align="center">
  <img src="assets/beepnado-cover.webp" alt="BeepNado — Your websites. Our radar. Uptime monitoring dashboard concept artwork" width="100%">
</p>

*Illustrative cover artwork. Uptime figures, monitored domains and incidents shown are example data.*

# BeepNado 🌪️ — Your websites. Our radar.

**BeepNado** is a self-hosted uptime monitoring platform for small businesses, freelancers, and web agencies. Built with Python, FastAPI, Docker, and SQLite, it tracks HTTP/HTTPS availability, response times, SSL certificate expiry, and outage/recovery history. Users can manage monitors in a secure dashboard and share public status pages. Email alerts require configured SMTP.

> **Launch status:** Beta release candidate. Public registration is disabled until email verification is configured; Stripe billing is not yet active.

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
curl -fI --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/
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
curl -fI --resolve app.beepnado.com:443:127.0.0.1 https://app.beepnado.com/
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

The development environment ran **14 tests** successfully; the interactive marketing layer passed desktop/mobile browser DOM tests. A Docker image was *not* built and the installer has not been executed against the live `yeet` server from this chat.
