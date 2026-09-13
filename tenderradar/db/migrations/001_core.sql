-- 001_core.sql — sources, crawl runs, raw archive, organizations, tenders.
-- Forward-only (§9). Never edit an applied migration; add a new one.

CREATE TABLE sources (
    id                      SERIAL PRIMARY KEY,
    name                    TEXT        NOT NULL,
    base_url                TEXT        NOT NULL,
    adapter_key             TEXT        NOT NULL UNIQUE,
    enabled                 BOOLEAN     NOT NULL DEFAULT TRUE,
    crawl_interval_min      INTEGER     NOT NULL DEFAULT 30,
    expected_yield_per_run  INTEGER     NOT NULL DEFAULT 0,
    last_success_at         TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE crawl_runs (
    id              BIGSERIAL PRIMARY KEY,
    source_id       INTEGER     NOT NULL REFERENCES sources (id),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    items_found     INTEGER     NOT NULL DEFAULT 0,
    items_new       INTEGER     NOT NULL DEFAULT 0,
    items_changed   INTEGER     NOT NULL DEFAULT 0,
    pages_fetched   INTEGER     NOT NULL DEFAULT 0,
    parse_failures  INTEGER     NOT NULL DEFAULT 0,
    -- §8.5: yield monitoring. Scrapers do not crash, they return zero.
    yield_anomaly   BOOLEAN     NOT NULL DEFAULT FALSE,
    error           TEXT
);

CREATE INDEX crawl_runs_source_started_idx
    ON crawl_runs (source_id, started_at DESC);

-- Append-only. Written BEFORE parsing, always (§6, §8.4). Never deleted.
CREATE TABLE raw_documents (
    id              BIGSERIAL PRIMARY KEY,
    source_id       INTEGER     NOT NULL REFERENCES sources (id),
    crawl_run_id    BIGINT      REFERENCES crawl_runs (id),
    url             TEXT        NOT NULL,
    kind            TEXT        NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT        NOT NULL,
    storage_path    TEXT        NOT NULL,
    content_type    TEXT,
    meta            JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX raw_documents_hash_idx ON raw_documents (content_hash);
CREATE INDEX raw_documents_source_fetched_idx
    ON raw_documents (source_id, fetched_at DESC);

CREATE TABLE organizations (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    name_bn     TEXT,
    parent_id   INTEGER REFERENCES organizations (id),
    -- PA = procuring agency, PE = procuring entity.
    type        TEXT CHECK (type IN ('MINISTRY', 'DIVISION', 'PA', 'PE')),
    ministry    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, type, parent_id)
);

CREATE INDEX organizations_parent_idx ON organizations (parent_id);

CREATE TABLE districts (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    name_bn     TEXT,
    division    TEXT
);

CREATE TABLE upazilas (
    id          SERIAL PRIMARY KEY,
    district_id INTEGER NOT NULL REFERENCES districts (id),
    name        TEXT    NOT NULL,
    name_bn     TEXT,
    UNIQUE (district_id, name)
);

CREATE TABLE tenders (
    id                          BIGSERIAL PRIMARY KEY,
    source_id                   INTEGER     NOT NULL REFERENCES sources (id),
    external_ref                TEXT        NOT NULL,
    reference_no                TEXT,
    package_no                  TEXT,
    title                       TEXT,
    title_bn                    TEXT,
    description                 TEXT,

    organization_id             INTEGER     REFERENCES organizations (id),
    -- Denormalized hierarchy: the PE string is what users recognize, and
    -- resolution to organization_id can lag behind ingest.
    organization_path           TEXT,
    district_id                 INTEGER     REFERENCES districts (id),
    upazila_id                  INTEGER     REFERENCES upazilas (id),
    district_name               TEXT,
    upazila_name                TEXT,

    procurement_nature          TEXT        NOT NULL DEFAULT 'other'
                                CHECK (procurement_nature IN
                                       ('goods', 'works', 'services', 'other')),
    procurement_type            TEXT,
    procurement_method          TEXT,

    -- e-GP publishes no official estimated cost; it stays NULL for that source.
    -- tender_security (~2-2.5% of estimate) is the value-range proxy.
    estimated_value             NUMERIC(18, 2),
    tender_security             NUMERIC(18, 2),
    document_price              NUMERIC(18, 2),

    published_at                TIMESTAMPTZ,
    closing_at                  TIMESTAMPTZ,
    opening_at                  TIMESTAMPTZ,
    document_last_selling_at    TIMESTAMPTZ,

    status                      TEXT        NOT NULL DEFAULT 'live'
                                CHECK (status IN
                                       ('live', 'closed', 'cancelled', 'awarded')),
    categories                  TEXT[]      NOT NULL DEFAULT '{}',
    lots                        JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- Free-text eligibility prose from the notice. On e-GP this arrives on the
    -- public detail page, so Phase 5 Layer 3 can start without a PDF parse.
    eligibility_text            TEXT,
    project_name                TEXT,
    app_id                      TEXT,
    detail_url                  TEXT,

    current_version_id          BIGINT,
    canonical_hash              TEXT        NOT NULL,
    first_seen_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail_fetched_at           TIMESTAMPTZ,

    -- Deduplication key: a tender is unique per source (§6).
    UNIQUE (source_id, external_ref)
);

CREATE INDEX tenders_closing_idx    ON tenders (closing_at) WHERE status = 'live';
CREATE INDEX tenders_published_idx  ON tenders (published_at DESC);
CREATE INDEX tenders_district_idx   ON tenders (district_name) WHERE status = 'live';
CREATE INDEX tenders_nature_idx     ON tenders (procurement_nature, status);
CREATE INDEX tenders_org_idx        ON tenders (organization_id);
CREATE INDEX tenders_categories_idx ON tenders USING GIN (categories);
-- Tenders whose detail page has not been pulled yet; drives the detail queue.
CREATE INDEX tenders_needs_detail_idx
    ON tenders (source_id) WHERE detail_fetched_at IS NULL;

CREATE TABLE tender_versions (
    id              BIGSERIAL PRIMARY KEY,
    tender_id       BIGINT      NOT NULL REFERENCES tenders (id) ON DELETE CASCADE,
    version_no      INTEGER     NOT NULL,
    changed_fields  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    canonical_hash  TEXT        NOT NULL,
    raw_document_id BIGINT      REFERENCES raw_documents (id),
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    change_type     TEXT        NOT NULL
                    CHECK (change_type IN
                           ('new', 'corrigendum', 'cancellation', 'extension')),
    UNIQUE (tender_id, version_no)
);

CREATE INDEX tender_versions_tender_idx
    ON tender_versions (tender_id, version_no DESC);

ALTER TABLE tenders
    ADD CONSTRAINT tenders_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES tender_versions (id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE tender_documents (
    id              BIGSERIAL PRIMARY KEY,
    tender_id       BIGINT      NOT NULL REFERENCES tenders (id) ON DELETE CASCADE,
    -- §8.3: we link to the original. storage_path stays NULL for government
    -- PDFs, which we do not rehost.
    url             TEXT        NOT NULL,
    doc_type        TEXT,
    storage_path    TEXT,
    parsed_at       TIMESTAMPTZ,
    UNIQUE (tender_id, url)
);

-- §8.6: every parser failure is replayable from the archive after a fix.
CREATE TABLE parse_failures (
    id              BIGSERIAL PRIMARY KEY,
    raw_document_id BIGINT      NOT NULL REFERENCES raw_documents (id),
    crawl_run_id    BIGINT      REFERENCES crawl_runs (id),
    adapter_key     TEXT        NOT NULL,
    error           TEXT        NOT NULL,
    traceback       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX parse_failures_unresolved_idx
    ON parse_failures (created_at DESC) WHERE resolved_at IS NULL;
