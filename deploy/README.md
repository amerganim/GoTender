# Deployment

One VPS, per §5. Everything here assumes Debian or Ubuntu and a single machine
running Postgres, the site, the crawler and the timers together.

Prefer a Bangladeshi or BDIX provider: a local IP hitting a local government
portal at a modest rate looks like ordinary traffic.

## Why this exists

The scheduler was started once inside a development session and was dead by the
next morning — one crawl, then seventeen hours of silence while the homepage
went on claiming a 30-minute refresh (§17).

**The 30-minute claim is the entire positioning, and it is only true while
something restarts the crawler.** That is what `tenderradar-scheduler.service`
is for. Gate 0's 72-hour unattended run cannot start until it is running here.

## First install

```bash
ssh root@your-vps
curl -fsSL https://raw.githubusercontent.com/amerganim/GoTender/main/deploy/install.sh -o install.sh
less install.sh          # read it before running it as root
bash install.sh
```

It will stop and tell you to write `.env`. Copy `.env.example`, then fill in:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql:///tenderradar` for the local socket |
| `ALERT_TOKEN_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` — rotating it invalidates every feedback link already emailed |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | rotating them invalidates every push subscription |
| `SITE_BASE_URL` | the real `https://` domain; relative links do not work in email |
| `CRAWLER_CONTACT_EMAIL` | real and monitored — it goes in the User-Agent (§8.1) |
| `EMAIL_BACKEND` | `smtp` in production; it defaults to `file` |
| `ADMIN_EMAILS` | who may open `/admin`; empty means nobody |

Then re-run `install.sh`.

## Two steps install.sh deliberately leaves to you

**TLS.** Push notifications and service workers require a secure context, so
without HTTPS the notification feature does not exist for users.

```bash
certbot --nginx -d your-domain
```

**Starting the crawler.** Confirm the schema is right first, then:

```bash
systemctl enable --now tenderradar-scheduler
journalctl -u tenderradar-scheduler -f
```

Watch the first sweep finish before walking away. That moment is the start of
Gate 0's 72-hour clock.

## What runs when

| Unit | Schedule | Purpose |
|---|---|---|
| `tenderradar-web` | always | the public site, behind nginx |
| `tenderradar-scheduler` | always, sweeps every 30 min | the freshness claim |
| `tenderradar-daily` | 02:00 UTC (08:00 Dhaka) | embed, match, send digests |
| `tenderradar-awards` | 20:00 UTC | contract award collection |
| `tenderradar-backup` | 22:00 UTC | pg_dump plus the irreplaceable tables |

Times are UTC because the server clock is UTC and timestamps are stored UTC
(§9). Do not change the server timezone to make these read nicer — display
conversion is the application's job.

The daily pipeline is one command rather than three timers because the order
matters: a digest built before matching sends yesterday's list, and matching
before embedding skips every tender crawled since the last run.

## Moving off Neon

Neon is fine for development, but a full sweep there took 23 minutes against
137ms round trips, which is close to the 30-minute crawl interval itself. On a
local socket the same sweep is limited by the portal, not the database.

```bash
NEON_URL='postgresql://...neon.tech/neondb?sslmode=require' \
DATABASE_URL='postgresql:///tenderradar' \
  bash deploy/migrate-from-neon.sh
```

It stops the writers, dumps, restores, and compares row counts table by table.
After it finishes, run one sweep and confirm it reports **0 new, 0 changed** —
a large "changed" count means the restore altered data, most likely NUMERIC
scale or timezone handling, and must be investigated before any digest goes
out.

Keep the Neon project for a week. It is free and it is the only rollback there
is.

## Checking it is actually working

```bash
systemctl status tenderradar-scheduler
sudo -u tenderradar /opt/tenderradar/.venv/bin/python -m tenderradar.cli health
journalctl -u tenderradar-daily --since yesterday
systemctl list-timers 'tenderradar*'
```

`health` is the one to watch. Freshness under 30 minutes, zero yield anomalies,
zero unresolved parse failures. A yield anomaly means a sweep returned far
fewer items than its trailing average, which is how a scraper fails — silently,
returning zero, while nobody complains because users never learn about tenders
they did not hear about (§8.5).

## Backups

`backup.sh` writes two things daily. A full custom-format dump, and a separate
plain dump of `users`, `user_profiles`, `feedback`, `contract_awards` and
`payments`.

That second file is the point. Everything else can be rebuilt by crawling
again; those tables cannot. Feedback is what match precision is computed from,
and awards cannot be backfilled from the future.

The script verifies each dump is readable with `pg_restore --list`, because a
backup nobody has ever restored is a hope rather than a backup. Copy them off
the machine — a backup on the same disk as the database protects against
almost nothing.

## Before launch

§8 carries a legal check that is **still outstanding**: read the e-GP terms of
use and find out whether BPPA offers an official data-sharing arrangement. The
crawler is polite, rate-limited and identifies itself with a real address, but
that is engineering, not legal cover.
