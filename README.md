# TenderRadar

Tender discovery and matching for Bangladeshi contractors and suppliers.

We tell you about a tender within 30 minutes, and only the ones you can
actually win.

See [CLAUDE.md](CLAUDE.md) for the project brief, phase gates and the rules
this code is built to. **Phase 0** (crawler and corpus) is in progress.

## Setup

Requires Python 3.12+ and a PostgreSQL 16 database.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux/macOS: .venv/bin/pip
cp .env.example .env
```

Fill in `.env`:

- `DATABASE_URL` — a Postgres connection string. Dev uses a Neon free-tier
  database (pgvector is preinstalled there, which Phase 2 needs). Production
  runs Postgres on the VPS.
- `CRAWLER_CONTACT_EMAIL` — a real address. Rule §8.1 requires the bot to
  identify itself, and this is appended to the User-Agent.

Then create the schema:

```bash
python -m tenderradar.db.migrate
```

Windows note: the CLI forces UTF-8 output itself, so Bangla tender text
prints correctly on a legacy console codepage. If you run ad-hoc Python
against this package outside the CLI, set `PYTHONUTF8=1` first.

## Commands

```bash
python -m tenderradar.cli probe egp_tender --pages 2   # fetch+parse, no DB writes
python -m tenderradar.cli crawl egp_tender             # one full crawl
python -m tenderradar.cli schedule                     # every 30 minutes
python -m tenderradar.cli health                       # freshness and yield
python -m tenderradar.cli replay <raw_document_id>     # re-parse after a fix
```

`probe` needs no database and is the fastest way to check the adapter still
works against the live portal.

## Tests

```bash
.venv/Scripts/python -m pytest
```

Tests cover the places that pay for themselves (§9): parsers against real
fixture HTML, deduplication and versioning, the raw archive, and yield
monitoring. There are no tests of framework behaviour.

## Layout

```
tenderradar/
  adapters/      source adapters — ALL source-specific logic lives here (§9)
    base.py      the fetch() -> bytes, parse() -> records contract
    egp.py       Bangladesh e-GP
  crawl/
    http.py      rate limiting, backoff, bot identification (§8)
    archive.py   append-only raw document store (§8.4)
    runner.py    orchestration + yield monitoring (§8.5)
    scheduler.py APScheduler, 30-minute interval
  db/
    migrations/  forward-only .sql, never edited after being applied
    repo.py      dedup, versioning, the list/detail merge rule
  models.py      normalized records shared across adapters
```

## How the e-GP adapter works

The portal's tender list is not in the HTML of `AllTenders.jsp`. That page
ships a jQuery handler that POSTs to `/TenderDetailsServlet` and splices the
returned `<tr>` fragment into a table. The adapter calls that endpoint
directly, so **no browser is needed** — a full sweep of the live pool is ~38
requests at `size=100`.

Three things about this source that the code depends on:

1. **Status codes are not a success signal.** Unknown paths return HTTP 200
   with a "Session Expired" page. The adapter checks content markers instead.
2. **A session is required.** The app hands out a `JSESSIONID` on first
   contact and rejects requests without one, so `fetch()` primes a session.
3. **The list fragment has no `<table>`.** An HTML5 parser silently discards
   `<tr>` outside a table, so the adapter wraps the fragment before parsing.
   Getting this wrong returns zero rows without raising anything.

### What e-GP does and doesn't publish

`estimated_value` is **not published** and stays NULL for this source.
`tender_security` (roughly 2–2.5% of the estimate) is the proxy to use for
value-range matching in Layer 1.

The detail page does carry the tender's eligibility prose — turnover,
experience, trade licence and liquid-asset clauses — in free HTML. That is
Phase 5 Layer 3 input available without parsing a single PDF, which matters
for cost trap #2.

### Package numbers

Most live rows carry **no package number** — the brief cell is just the
description. A package number, when present, is a single whitespace-free token
containing a digit or a slash (`PSWSC-6145`, `LGED/GOBM/SRJ/26-27/RW-49`), so
that is how the parser detects one. Treating the first line as a package number
unconditionally shifted every field by one for the majority of tenders.

### Live vs detail merging

A list row carries a subset of fields; the detail page carries all of them.
Upserts **merge** — a missing value never overwrites a known one — and the
canonical hash is computed on the merged result. Without this, every 30-minute
sweep would null out district and security, flip the hash back and forth, and
manufacture a fresh "corrigendum" for every tender on every crawl.

That alone proved insufficient in practice. The two views genuinely *disagree*
on some fields: the list renders a title as "Procurement of surgical equipment"
where the detail page says "Procurement of Surgical Equipment". So once a
detail page has been seen, a list sweep may only **fill gaps** — it can never
contradict detail data.

Money needs care for the same reason. `NUMERIC(18,2)` returns `800000.00` where
the parser produced `800000`; they are equal as Decimals but differ as text, so
an unquantized hash flips on every reload. Amounts are quantized before
hashing, and the upsert refuses to write a version whose field diff is empty —
a hash change with no field change is a serialization bug, never a corrigendum.

**The check that matters:** run a crawl twice. The second must report
`0 new, 0 changed`. Run it after touching the parser, the upsert or the hash.

## Rules this code will not break

- Raw bytes are archived **before** parsing, always, and `raw_documents` is
  never deleted (§8.4).
- Government PDFs are linked, never rehosted (§8.3).
- Yield is monitored, not uptime — a run returning far fewer items than its
  trailing average is flagged as a silent failure (§8.5).
- Every parse failure records its `raw_document_id` so it can be replayed
  after a fix (§8.6).
- All timestamps stored UTC, displayed Asia/Dhaka. Text is NFC-normalized on
  ingest (§9).

## Deploying

See [deploy/README.md](deploy/README.md). One VPS: Postgres, the site, the
crawler and the timers on one machine, per §5.

The unit that matters is `tenderradar-scheduler`. It IS the 30-minute
freshness claim, and the claim is only true while something restarts it — a
scheduler started in a terminal died overnight and left the homepage
advertising a refresh that was not happening.

```bash
python -m tenderradar.cli daily    # embed, match, digest -- in that order
python -m tenderradar.cli awards   # contract award collection
```

## Before launch

⚠️ The legal check in §8 is **not done**. Read the e-GP terms of use and find
out whether BPPA offers an official data-sharing arrangement before this goes
public. This code is written to be a polite, identifiable, rate-limited
client, but that is engineering, not legal cover.
