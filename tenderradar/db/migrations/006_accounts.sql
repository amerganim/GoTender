-- 006_accounts.sql — accounts, profiles, matches, alerts, feedback (Phase 2).
--
-- Deliberately excludes the embedding tables: their vector(N) dimension is
-- fixed by the embedding model, and picking N before that is settled would
-- mean re-embedding the whole corpus to change it. They land in their own
-- migration once the model is chosen.

-- Phone-primary, email secondary: this market is phone-first (§4).
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    phone           TEXT        NOT NULL UNIQUE,
    email           TEXT,
    name            TEXT,
    company_name    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at     TIMESTAMPTZ,
    last_seen_at    TIMESTAMPTZ,
    -- Free until Gate 3 proves anyone pays; no gateway before then (§3).
    plan            TEXT        NOT NULL DEFAULT 'free'
                    CHECK (plan IN ('free', 'paid', 'premium')),
    plan_expires_at TIMESTAMPTZ,
    -- A user who stops opening digests is the churn signal that matters.
    unsubscribed_at TIMESTAMPTZ
);

CREATE INDEX users_active_idx ON users (id) WHERE unsubscribed_at IS NULL;

CREATE TABLE user_profiles (
    user_id                 BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    -- The free-text description is the input to semantic matching (§7).
    business_description    TEXT,
    district_names          TEXT[]  NOT NULL DEFAULT '{}',
    category_keywords       TEXT[]  NOT NULL DEFAULT '{}',
    procurement_natures     TEXT[]  NOT NULL DEFAULT '{}',
    procurement_methods     TEXT[]  NOT NULL DEFAULT '{}',
    -- e-GP publishes no estimated value, so these bound tender_security,
    -- which runs about 2-2.5% of the estimate.
    min_security            NUMERIC(18, 2),
    max_security            NUMERIC(18, 2),
    annual_turnover         NUMERIC(18, 2),
    experience_summary      TEXT,
    enlistment_categories   TEXT[]  NOT NULL DEFAULT '{}',
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE matches (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    tender_id       BIGINT      NOT NULL REFERENCES tenders (id) ON DELETE CASCADE,
    score           REAL        NOT NULL,
    layer1_pass     BOOLEAN     NOT NULL DEFAULT TRUE,
    layer2_score    REAL,
    layer3_eligibility TEXT     CHECK (layer3_eligibility IN
                                 ('qualified', 'not_qualified', 'jv_only', 'unknown')),
    reasons         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A tender is matched to a user at most once; re-running the engine
    -- updates the score rather than spamming a second alert.
    UNIQUE (user_id, tender_id)
);

CREATE INDEX matches_user_score_idx ON matches (user_id, score DESC);
CREATE INDEX matches_tender_idx ON matches (tender_id);

CREATE TABLE alerts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    channel     TEXT        NOT NULL
                CHECK (channel IN ('email', 'push', 'whatsapp')),
    tender_ids  BIGINT[]    NOT NULL DEFAULT '{}',
    sent_at     TIMESTAMPTZ,
    opened_at   TIMESTAMPTZ,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX alerts_user_sent_idx ON alerts (user_id, sent_at DESC);

-- "THE most important table in this schema" (§6). Match precision is
-- thumbs-up divided by alerts sent, and below 30% nothing else matters.
CREATE TABLE feedback (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    tender_id   BIGINT      NOT NULL REFERENCES tenders (id) ON DELETE CASCADE,
    verdict     TEXT        NOT NULL CHECK (verdict IN ('up', 'down')),
    alert_id    BIGINT      REFERENCES alerts (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One verdict per user per tender; a changed mind overwrites it.
    UNIQUE (user_id, tender_id)
);

CREATE INDEX feedback_user_idx ON feedback (user_id, created_at DESC);
CREATE INDEX feedback_verdict_idx ON feedback (verdict, created_at DESC);

-- Manual until Phase 4: bKash personal, recorded by hand (§4).
CREATE TABLE payments (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT      NOT NULL REFERENCES users (id),
    amount          NUMERIC(12, 2) NOT NULL,
    method          TEXT        NOT NULL,
    trx_id          TEXT        NOT NULL UNIQUE,
    period_months   INTEGER     NOT NULL,
    recorded_by     TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    note            TEXT
);
