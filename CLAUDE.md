# CLAUDE.md — TenderRadar (working name)

> Project brief and build plan. Claude Code reads this file automatically.
> Keep it updated. When a decision changes, change it **here** first.

---

## 1. What this is

A tender discovery and matching service for Bangladeshi contractors and suppliers
who bid on government and private tenders.

**One sentence:** everyone else tells you a tender exists tomorrow morning; we tell
you within 30 minutes, and only the ones you can actually win.

**Operator:** one developer, part time, near-zero budget, based in Dhaka.
Every architectural decision below assumes exactly one person maintains this.

---

## 2. Market facts (verified Sep 2026 — re-verify before relying on these)

- Bangladesh public procurement ≈ **USD 30 billion/year**.
- e-GP crossed **1,000,000 cumulative tenders**; ~**20,000 invited per month** at peak.
- **143,374 registered tenderers** (up from 120,777 the prior year).
- Live tender pool at any moment ≈ **8,500**, plus ~1,400 auctions.

### Competitors

| | BDTender | Alltender |
|---|---|---|
| Age | ~19 years | established |
| Refresh rate | frequent | **once per 24h (~11:00 AM)** |
| Filters | category, district, organization | item, department, district, method, source |
| Channels | Email + WhatsApp | Email |
| Apps | Android + iOS | Android |
| Payments | bKash merchant, SSLCommerz, bank transfer, **bKash/Nagad personal** | SSLCommerz |
| Side business | e-GP training, ERP, school software, web dev | e-GP training, bidding service, "Tender Manager" staffing |

**BDTender pricing:** ৳299 / 5-day trial · ৳1,050 / month · ৳2,835 / 3mo ·
৳5,040 / 6mo · **৳8,190 / 12mo (~৳683/mo)** · ৳9,999 Premium (alerts + training +
12 tender submissions). Also a free verified tier ("জিতাও").

### Three exploitable weaknesses

1. **Latency.** Alltender publicly timestamps a 24-hour refresh cycle. We crawl
   every 30 minutes and display our last-crawl time on the homepage.
2. **Matching.** Both use taxonomy filters only. Users drown in irrelevant alerts.
   Semantic matching + eligibility filtering is a real capability gap.
3. **No tier ladder.** BDTender's plans are feature-identical, differing only in
   duration. They have nothing to upsell. We build free → paid → premium.

### What their business model tells us

Alerts are a **lead magnet**. Their margin is in training, submission services,
staffing, and publishing fees. We are deliberately building only the lead magnet,
so our pricing ceiling is lower and our cost base must stay near zero.

---

## 3. Strategy

**Positioning:** fastest and most relevant, not most comprehensive.

**Price anchor:** ৳700–1,000/month, annual-first. Do not price above BDTender
without a demonstrable reason.

**Free tier is not a differentiator** — BDTender already has one. Free browsing
exists to (a) earn SEO traffic and (b) let a skeptical contractor verify our
coverage before paying.

### Anti-goals — do not build these

- ❌ e-GP / PPR training (classroom business)
- ❌ Tender publishing service (needs outbound sales to procuring entities)
- ❌ Done-for-you bidding or "tender manager" staffing
- ❌ ERP, accounting, school software
- ❌ Mobile apps before Phase 4 (responsive web is enough)
- ❌ International tenders (low local demand, high effort)
- ❌ SMS (see cost trap #1)
- ❌ Payment gateway integration before Gate 2

### Three cost traps — architectural rules, not preferences

1. **Never send per-tender SMS.** 500 users × 5 alerts/day = ~৳56,000/month.
   Same users on one daily digest = ~৳11,000. Use email and web push (both free)
   until revenue justifies otherwise.
2. **Never run an LLM over every tender document.** 20,000 docs/month at full
   length costs ৳30,000–90,000. Parse only documents already matched to ≥1 user,
   cache the parse per tender, and try regex extraction before falling back to a model.
3. **Never buy ads before match precision is tuned.** Paid traffic into a bad
   matching engine converts nothing and burns the budget you don't have.

---

## 4. Phases

Each phase has an **exit gate**. Do not start the next phase until the gate passes.
If a gate fails, the correct move is to fix the current phase or stop — not to
add features from a later phase.

---

### Phase 0 — Crawler and corpus (weeks 1–2)

**Goal:** a complete, fresh, queryable copy of every live e-GP tender.

Build:
- Source adapter framework (`fetch()` → raw bytes, `parse()` → normalized records)
- e-GP adapter: tender search, corrigenda, cancellations
- Raw document archive (store before parsing, always)
- Canonical `tenders` table + `tender_versions` for amendments
- Deduplication
- Crawl scheduler, every 30 minutes
- Adapter health monitoring

**Gate 0:** live tender count is within 5% of Alltender's published figure, and the
crawler has run unattended for 72 hours without silent failure.

Budget: ~৳650/month.

---

### Phase 1 — Public site and SEO surface (week 3)

**Goal:** a browsable, indexable tender directory. No accounts yet.

Build:
- Tender list + detail pages, server-rendered
- Browse by category, organization, district, upazila, source, tender method
- Closing-today / closing-this-week view
- Full-text search
- **Homepage: live tender count + last-crawl timestamp, prominent**
- Programmatic SEO pages (one per organization × district × category combination
  that has ≥5 historical tenders)
- `sitemap.xml`, structured data, Bangla + English meta

**Gate 1:** 200+ indexed pages, site loads under 2s on 3G, and the homepage makes
the freshness claim verifiable at a glance.

Budget: ~৳800/month.

---

### Phase 2 — Accounts and matching (weeks 4–6)

**Goal:** the actual product. A contractor gets a short, relevant daily list.

Build:
- Registration (phone-primary, email secondary — this market is phone-first)
- Business profile: free-text description, districts, categories, procurement
  types, value range, optionally turnover and experience
- **Matching engine** (see §7)
- Daily digest email
- **Thumbs up/down on every alert** — ship this in the very first email
- Web push notifications (free, unlimited, works well on Android Chrome)
- Simple admin dashboard: signups, open rates, match precision

**Gate 2 — the one that matters:**
- 50+ signups from free channels only
- Week-3 digest open rate ≥ 40%
- Match precision (thumbs-up ÷ alerts sent) ≥ 30%

If open rate is low, the matching is wrong. Fix matching. Do not add features.

Budget: ~৳900/month.

---

### Phase 3 — The money test (week 7)

**Goal:** find out if this is a business. No new features.

- Manual outreach to every active user:
  *"Founding member: ৳3,500 for 6 months, locked forever. bKash to this number,
  send the TRX ID."*
- Collect via **bKash personal**, record in a spreadsheet, activate by hand.
  (BDTender still accepts personal bKash after 19 years. This is normal here.)

**Gate 3:**
- ≥ 10 pay → it's a business. Proceed to Phase 4, get a trade license, talk to an accountant.
- 3–9 pay → close. Interview every decliner before building anything else.
- < 3 pay → alerts are not the painkiller. Ask those 50 people what *would* be
  worth ৳1,000/month. Their answer is worth more than the code already written.

Budget: ~৳900/month. Break-even at **2 paying users**.

---

### Phase 4 — Monetize and broaden (months 3–4)

Only after Gate 3 passes.

- Subscription tiers: Free browse / Paid alerts / Premium
- Payment gateway (compare aamarPay, ShurjoPay, UddoktaPay before SSLCommerz —
  SSLCommerz setup is ৳25,500 non-refundable)
- WhatsApp Business API (users expect it as standard, not premium)
- Additional sources: LGED, RHD, BPDB, DESCO, WASA, universities, state banks
- Sales and auction notices
- Saved searches, tender calendar
- Trade license, TIN, business bank account

**Gate 4:** 50 paying users, monthly churn under 10%.

---

### Phase 5 — The defensible layer (months 5+)

This is where we stop being a fourth notification portal.

- **Eligibility engine.** Parse turnover, similar-work experience, and licensing
  requirements from tender documents; score against the user's profile as
  Qualified / Not qualified / JV only, quoting the exact failing clause.
- **Award intelligence.** Crawl NOA/contract awards. Answer: who wins from this PE,
  how many bidders typically compete, what the typical winning discount is,
  which PEs have thin competition.
- **APP early warning.** Annual Procurement Plans reveal intent months before a
  tender notice. Nobody covers this.
- **Document vault** with expiry alerts (trade license, TIN, VAT, bank solvency,
  experience certificates).
- **Win/loss analytics** per user.
- Android app (Play Store).

Award intelligence compounds — every month of collection makes it harder to copy.
Prioritize it over everything else in this phase.

---

## 5. Stack

Chosen for solo maintainability. Swap any layer for whatever you're fastest in,
but keep the shape.

- **Language:** Python 3.12
- **Web:** FastAPI + Jinja2 + HTMX (server-rendered — SEO is a core requirement,
  so avoid a client-side SPA)
- **DB:** PostgreSQL 16 + `pgvector` (one database, no separate vector store)
- **Fetching:** `httpx` by default; Playwright **only** for pages that genuinely
  require JS execution
- **Scheduling:** APScheduler in-process, backed by a `crawl_runs` table.
  No Celery, no Redis, no queue broker until there's a reason.
- **Storage:** raw archive on VPS disk, gzipped. Move to Cloudflare R2 when >20GB.
- **Email:** Brevo or Resend free tier
- **Hosting:** single VPS. Prefer a Bangladeshi/BDIX provider — a local IP hitting
  a local government portal at a modest rate looks like ordinary traffic.
- **Analytics:** PostHog free tier (funnels + session replay)
- **Errors:** Sentry free tier

Everything above fits in ~৳800/month. Do not add infrastructure that doesn't.

---

## 6. Data model

```
sources              id, name, base_url, adapter_key, enabled, crawl_interval_min,
                     expected_yield_per_run, last_success_at

crawl_runs           id, source_id, started_at, finished_at, status,
                     items_found, items_new, items_changed, error

raw_documents        id, source_id, url, fetched_at, content_hash,
                     storage_path, content_type
                     -- written BEFORE parsing, always

organizations        id, name, name_bn, parent_id, type (PA|PE), ministry

tenders              id, source_id, external_ref, package_no, title, title_bn,
                     description, organization_id, district_id, upazila_id,
                     procurement_nature (goods|works|services),
                     procurement_method (OTM|LTM|EOI|RFP|other),
                     estimated_value, tender_security, document_price,
                     published_at, closing_at, opening_at,
                     status (live|closed|cancelled|awarded),
                     current_version_id, first_seen_at, canonical_hash

tender_versions      id, tender_id, version_no, changed_fields (jsonb),
                     raw_document_id, observed_at, change_type
                     (new|corrigendum|cancellation|extension)

tender_documents     id, tender_id, url, doc_type, storage_path, parsed_at

tender_embeddings    tender_id, embedding vector(N), model, generated_at

users                id, phone, email, name, company_name, created_at,
                     verified_at, plan, plan_expires_at

user_profiles        user_id, business_description (free text),
                     district_ids[], category_ids[], procurement_natures[],
                     min_value, max_value, annual_turnover,
                     experience_summary, enlistment_categories[]

profile_embeddings   user_id, embedding vector(N), model, generated_at

matches              id, user_id, tender_id, score, layer1_pass, layer2_score,
                     layer3_eligibility, reasons (jsonb), created_at

alerts               id, user_id, channel (email|push|whatsapp),
                     tender_ids[], sent_at, opened_at

feedback             id, user_id, tender_id, verdict (up|down), created_at
                     -- THE most important table in this schema

payments             id, user_id, amount, method, trx_id, period_months,
                     recorded_by, recorded_at
                     -- manual until Phase 4
```

**Rule:** `raw_documents` is append-only and never deleted. Re-parsing history
without re-crawling is the difference between a system you can evolve and one you
rewrite every six months.

---

## 7. Matching engine

Three layers, in order. Cheap filters first.

**Layer 1 — hard filters (SQL).** District/upazila, organization, procurement
nature, method, estimated value range, closing window. Never surface a Sylhet
tender to a Rajshahi-only contractor. This eliminates 90%+ of candidates for
almost no cost.

**Layer 2 — semantic similarity (pgvector).** Embed the user's free-text business
description; embed each tender's title + description. Rank by cosine similarity.

> Use a **multilingual** embedding model. Real data is mixed Bangla/English with
> inconsistent transliteration ("বৈদ্যুতিক" / "electrical" / "boiddutik").
> Keyword matching collapses on this; embeddings are the entire reason this works.

**Layer 3 — eligibility (Phase 5).** Parse turnover / experience / licensing from
the tender document. Compare against profile. Even 60% coverage is a feature no
competitor has. Start with the numeric turnover requirement alone — regex first,
LLM only as fallback, result cached per tender and shared across all users.

**Feedback loop.** Every `feedback` row adjusts a per-user weighting. After a month
the system knows a user's real preferences better than they articulated them.

**Primary metric: match precision = thumbs-up ÷ alerts sent.** Below 30% and
nothing else in this document will save the business.

---

## 8. Crawler rules — non-negotiable

1. Respect `robots.txt`. Rate-limit. Identify the bot with a real contact email.
2. Cache aggressively; use conditional requests; never re-fetch unchanged pages.
3. **Link to the original tender document. Do not rehost government PDFs.**
4. Store raw before parsing. Always.
5. **Monitor yield, not uptime.** Scrapers do not crash — they silently return
   zero. Compare each run's item count against that source's trailing average
   and alert on anomaly. Users never complain about tenders they didn't hear
   about; they just quietly stop paying. This is the #1 killer of aggregators.
6. Every parser failure writes a row with the `raw_document_id` so it can be
   replayed after a fix.
7. Back off on 429/503. Never retry aggressively against a government host.

> ⚠️ **Legal check before launch.** Read the e-GP terms of use and check whether
> BPPA offers any official data-sharing arrangement. The incumbents clearly obtain
> this data somehow, but "somehow" is a poor foundation. Get this reviewed by
> someone qualified — this file is not legal advice.

---

## 9. Conventions for Claude Code

- **Small, reviewable commits.** One concern per commit.
- **No new infrastructure dependencies** without updating §5 here first.
- **Adapters are pluggable.** Never write source-specific logic outside an adapter.
- Tests where they pay for themselves: parsers (fixture HTML → expected records),
  dedup, matching. Skip test theatre elsewhere.
- Bangla text everywhere: UTF-8 end to end, normalize Unicode on ingest, never
  assume ASCII in slugs or search.
- All timestamps stored UTC, displayed Asia/Dhaka.
- Secrets in env vars. Never commit credentials.
- Migrations are versioned and forward-only.
- When a task would add something on the anti-goals list (§3), stop and say so
  instead of building it.

---

## 10. Metrics

Track these. Ignore everything else.

| Metric | Target | Why |
|---|---|---|
| Crawl freshness | < 30 min | The core claim |
| Coverage vs Alltender count | within 5% | Credibility |
| Adapter yield anomaly | 0 silent failures | Existential |
| Visitor → signup | — | Is the pitch clear? |
| **Week-3 digest open rate** | **≥ 40%** | Everyone opens week 1. Week 3 is truth. |
| **Match precision** | **≥ 30%** | The product |
| Trial → paid | ≥ 20% | The business |
| Monthly churn | < 10% | Phase 4 gate |

Site visits and signup counts are vanity metrics. Do not optimize for them.

---

## 11. Budget

| Phase | Monthly | Break-even users @ ৳683/mo |
|---|---|---|
| 0–3 | ~৳800 | **2** |
| 4 | ~৳12,000 | ~18 |
| 5 | ~৳33,000 + ads | ~50 |

One-time, deferred until Gate 3 passes: trade license (৳1,000–10,000), payment
gateway setup (SSLCommerz ৳25,500 — check cheaper alternatives first), domain
(৳1,500/yr).

---

## 12. Open risks

| Risk | Mitigation |
|---|---|
| e-GP blocks or changes markup | Adapter isolation, raw archive, yield monitoring, local IP |
| Legal exposure from scraping | Review ToS before launch; link don't rehost; seek qualified advice |
| Matching isn't better in practice | Gate 2 kills the project early and cheaply |
| Market buys by phone, not landing page | Publish a phone number; expect to answer it |
| Incumbents copy the 30-min refresh | Move to award intelligence (Phase 5) before they react |
| Solo founder burnout | Gates exist to permit stopping. Stopping at a failed gate is a success. |

---

## 13. Phase status

| Phase | State |
|---|---|
| 0 — Crawler and corpus | **Built.** Gate 0 NOT passed |
| 1 — Public site and SEO | **Built.** Gate 1 partially evidenced |
| 2 — Accounts and matching | Not started |
| 3 — Money test | Not started (not code) |
| 4 — Monetize and broaden | Not started |
| 5 — Defensible layer | Not started |

**Gate 0 is not passed** and must not be quietly skipped:
- Coverage parity needs an offline-tender adapter; the e-tender pool is ~3,740
  against the ~8,500 figure in §2, which includes offline tenders and auctions.
- No 72-hour unattended run has happened. The longest run to date is one full
  sweep of 23 minutes.

**Gate 1 evidence so far:** 4,027 sitemap URLs against the 200+ requirement,
and the homepage states minutes-since-crawl plus the live count on every page.
Load time under 2s on 3G is NOT yet measured.

Phase 1 was built before Gate 0 passed, at the operator's explicit request, to
make crawl quality inspectable. That was a deliberate exception, not a
precedent.

## 14. Embedding model evaluation (Sep 2026) — amends §7

Measured, not assumed. 400 real tender titles from the corpus, seven realistic
contractor profiles in English, Bangla and Banglish, scored by precision@10.

**Model chosen:** `paraphrase-multilingual-mpnet-base-v2`, 768 dimensions, run
locally through ONNX (`fastembed`, no PyTorch). Free, ~1GB, no per-tender API
cost, nothing leaves the VPS.

### Four findings that change §7

1. **The corpus contains no Bangla at all.** 0.000% Bangla characters across
   3,739 tenders. §7 says "real data is mixed Bangla/English"; for the English
   e-GP interface we crawl, it is not. The portal has a Bangla interface we do
   not use — worth revisiting. The Bangla problem is therefore on the **query**
   side (a contractor's own description), not the document side.

2. **Cross-lingual matching works at realistic length, and fails on short
   phrases.** Bangla profiles scored 70% against English profiles' 73%. But
   two-word Bangla phrases ranked an unrelated tender above the right one.
   Profile quality matters: collect a few sentences, not keywords.

3. **`multilingual-MiniLM` (384d) is unusable here.** Its Bangla embeddings
   collapse — two unrelated Bangla phrases scored 0.440, higher than the 0.404
   between a genuine cross-language pair. Its English is fine. Do not use it
   for this market.

4. **Layer 2 must be hybrid, not vector-only.** Embeddings scored 10/10 on
   water and medical in both languages, but 2/10 on electrical, because a
   generically-worded profile ("we are a contractor... for government offices")
   matches boilerplate rather than the domain. Postgres full-text search scored
   **10/10 on that same electrical query**. The two fail in different places:
   embeddings carry meaning and language, FTS carries exact domain terms.
   Combine them and keep the FTS index from Phase 1.

## 15. Gate 0 coverage — measured, and §2's 8,500 does not hold

The offline-tender adapter is built, and it does **not** close the gap §2
implies. Measured 2026-09-14 by asking the portal itself how many pages each
view holds:

| e-GP view | Pages | Approx tenders | Ours |
|---|---|---|---|
| e-tender, Live | 38 | ~3,800 | **3,750** |
| e-tender, Archived | 6,262 | ~626,200 | not crawled |
| e-tender, Cancelled | 64 | ~6,400 | not crawled |
| Offline tenders, all | 34 | ~680 | **662** (only 42 live) |

**Live coverage of what e-GP actually publishes is ~99%.** The offline feed
turned out to be mostly historical: 620 of its 662 notices had already closed.

So the ~8,500 "live tender pool" in §2 is not the e-GP live count and cannot be
reached by crawling e-GP harder. It is measuring something else — plausibly
live plus cancelled, or a competitor counting archived notices, or a figure
that was never verified. **Gate 0's "within 5% of Alltender's published figure"
therefore needs re-basing on a number we can verify**, and the honest
candidate is e-GP's own Live view, against which we currently sit at ~99%.

Do not add more adapters chasing 8,500 until that figure is checked against
Alltender's live site. Building sources to hit a number nobody has verified is
how the budget gets spent on the wrong thing.

## 16. Award intelligence — collection started early, deliberately

§4 places award intelligence in Phase 5 and says to prioritise it because it
compounds. It is the one item in this project with a genuine time value:
**awards cannot be backfilled from the future.** Every month not collecting is
a month of history permanently lost.

Collection therefore starts now. The FEATURES built on it stay behind Gate 4 —
storing rows is not shipping a product.

Source: `POST /SearchNoaServlet`, ~43,873 pages at size 20 (~877,000 awards),
newest first. Each row carries the winner, the procuring entity, the district,
the signing date and the contract value, and links back to a tender id.

Two things to know about the data:

* **Value is published in crore BDT, but the column is labelled "(Cr. BDT)
  /(Other Currency)".** The figure is stored exactly as published and never
  converted, because a wrong multiplier would silently corrupt every analysis
  built on it.
* **Awards do not yet join to our tenders.** 0 of the first 400 matched, which
  is correct: awards are for tenders already closed and decided, while our
  corpus holds live ones. The join populates as today's live tenders get
  awarded over the coming weeks — which is the compounding effect itself.

From the first 400 rows (0.05% of the archive) the §4 questions already answer
themselves: repeat winners, and procuring entities where every award goes to a
single firm.

## 17. The scheduler must run on the VPS, not in a dev session

Demonstrated, not theorised. The scheduler was started inside a development
session on 2026-09-13 at 19:33 UTC and was dead by the next morning, having
crawled once. Seventeen hours with no crawl, while the homepage went on
claiming a 30-minute refresh.

**Gate 0's 72-hour unattended run cannot be started this way.** It needs a
systemd unit on the VPS with restart-on-failure. Until then the freshness claim
is only true while someone is watching, which is the opposite of the promise.

## 18. Legal check (§8) — research done, review still outstanding

Findings are in [docs/legal-review.md](docs/legal-review.md). Researched
2026-09-14 against the live portal. **Not legal advice**; §8's instruction to
get a qualified review still stands and is not discharged by that document.

The four things worth knowing without opening it:

1. **The terms read as a registered-user agreement**, scoped to "e-GP user
   services" and followed entirely by registration, credentials and payment
   clauses. Nothing addresses scraping, crawl rate or redistribution by name.
   That silence is the central question, not an answer.
2. **There is a broad copyright assertion** covering the information on the
   portal, not only its software. Whether that reaches the *facts* in a tender
   notice — closing dates, package numbers, entity names — or only the
   compilation is the question that decides what we may store and re-display.
3. **There is no robots.txt at all**; the path returns the session page. So
   §8.1's "respect robots.txt" is vacuous here, as already noted in §13.
4. **BPPA has no public API, but it does have a statutory RTI route** —
   a designated Information Officer, an appeals path, and published RTI rules,
   under the Right to Information Act 2009. That turns §8's "somehow" into a
   named official channel.

**Recommended sequence:** file the RTI request with BPPA's Designated Officer
first (cheap, official, produces a written answer from the data owner), then
take the copyright and binding questions to a Bangladeshi IP/IT lawyer with the
brief in hand. Both before the site is public — which Gate 1 means is not
imminent anyway.

A draft request is ready at [docs/rti-request.md](docs/rti-request.md). It must
be filed in the operator's own name; the procedural details (form, fee,
deadlines) need checking against the current rules before filing.

Worth naming the upside: if BPPA will grant an official data-sharing
arrangement, that is worth more than the crawler. It is the one moat a
competitor scraping the same pages could not copy. The request is written to
make saying yes easy.

## 19. Right now

Phase 0, task 1 is **built**: source adapter framework, e-GP tender-search
adapter, raw archive, dedup + versioning, crawl scheduler, yield monitoring.
See README.md for commands and the adapter's behaviour.

**Verified end to end against Neon (Postgres 18) on 2026-09-13:** migrations
apply, a crawl writes tenders, versions, raw documents and crawl runs, and
three consecutive sweeps report `0 new, 0 changed`. `health` reports freshness
and per-run yield.

**Next:** widen from `--pages 2` to the full 38-page sweep and start the
72-hour unattended run that Gate 0 requires.

### Three bugs the first real crawl exposed — all fixed, all with tests

These only appeared once data round-tripped through Postgres. Parser unit tests
could not have caught the last two.

1. **`package_no` was the description for ~79% of tenders.** The list cell is
   `[nature, package_no, description]` only when a package number exists;
   most rows are just `[nature, description]`, so every field shifted by one.
   Package numbers are now detected by shape (one whitespace-free token
   containing a digit or slash). Migration `002` repairs affected rows.
2. **List sweeps overwrote authoritative detail data.** The list and detail
   views genuinely disagree — different capitalisation of the same title, and
   the list omits package numbers. "Never overwrite with NULL" was not enough;
   a list sweep now may only fill gaps once a detail page has been seen.
3. **Money broke the canonical hash.** `NUMERIC(18,2)` returns `800000.00`
   where the parser produced `800000`. Equal as Decimals, different as text,
   so the hash flipped on every reload and wrote a version whose
   `changed_fields` was empty. Money is now quantized before hashing, and the
   upsert refuses to write a version with an empty diff — a hash change with no
   field change is always a serialization bug, never a corrigendum.

Each would have produced a corrigendum for every affected tender on every
30-minute sweep: thousands of phantom amendments a day, destroying both the
alert product and the Gate 2 match-precision signal. **Re-running a sweep and
asserting `0 new, 0 changed` is the single most valuable check in this
project** — run it after any change to the parser, the upsert or the hash.

### Source recon findings (Sep 2026) — these amend sections above

1. **e-GP needs no browser.** The tender list comes from a plain
   `POST /TenderDetailsServlet`; `AllTenders.jsp` only renders it via jQuery.
   A full live sweep is ~38 requests. Playwright is not needed for this source.

2. **The live e-tender pool is ~3,770, not 8,500** (§2). The larger figure must
   include offline tenders (`SearchTenderOffline.jsp`) and auctions. **Gate 0's
   "within 5% of Alltender's figure" cannot be met from the e-tender adapter
   alone** — an offline-tender adapter is required for coverage parity, and is
   the next adapter to write.

3. **e-GP publishes no estimated value.** §6's `estimated_value` stays NULL for
   this source. `tender_security` (~2-2.5% of the estimate) is the proxy for
   Layer 1 value-range filtering. Profile value ranges should be collected and
   matched with this in mind.

4. **Eligibility prose is on the free detail page** — turnover, experience,
   trade licence and liquid-asset clauses, in HTML. Phase 5's Layer 3 can start
   on this text without parsing any PDF, which removes most of cost trap #2's
   exposure for the first cut of the eligibility engine. It is already captured
   into `tenders.eligibility_text` from Phase 0.

5. **Other public endpoints found, unused so far:** `SearchNOA.jsp` (awards -
   Phase 5 award intelligence), `SearchAPP.jsp` (annual procurement plans -
   Phase 5 early warning), `SearchTenderOffline.jsp`, `SearcheCMS.jsp`,
   `SearchAwardedContractOffline.jsp`. Award intelligence compounds with time,
   so starting its collection early is worth more than it costs, even though
   the feature ships in Phase 5.

6. **There is no robots.txt** on eprocure.gov.bd — the path returns a session
   page with HTTP 200. §8.1's "respect robots.txt" is therefore vacuous here;
   rate limiting and bot identification are what remain, and both are enforced
   in `crawl/http.py`. **The legal review in §8 is still outstanding.**
