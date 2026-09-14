-- 009_awards.sql — contract awards (§4 Phase 5, started early on purpose).
--
-- Phase 5 says award intelligence compounds and to prioritise it, and this is
-- the one item in the project with a genuine time value: awards cannot be
-- backfilled from the future. Every month not collecting is a month of history
-- permanently lost, and the archive is what makes the feature hard to copy.
--
-- Collection starts now; the FEATURES built on it stay in Phase 5 behind
-- Gate 4. Storing rows is not the same as shipping a product.
--
-- Answers the questions §4 poses: who wins from this procuring entity, which
-- entities have thin competition, what contracts typically go for.

CREATE TABLE contract_awards (
    id                  BIGSERIAL PRIMARY KEY,
    source_id           INTEGER     NOT NULL REFERENCES sources (id),

    -- The portal's tender id. Same value space as tenders.external_ref for the
    -- egp_tender source, so awards join back to notices we already hold --
    -- deliberately NOT a foreign key, because an award may reference a tender
    -- published before we started crawling.
    tender_external_ref TEXT,
    pkg_lot_id          TEXT,
    reference_no        TEXT,
    title               TEXT,

    ministry            TEXT,
    procuring_entity    TEXT,
    procurement_method  TEXT,
    district_name       TEXT,

    -- THE column this table exists for.
    winner              TEXT        NOT NULL,

    -- The portal labels this "Value (Cr. BDT) /(Other Currency)", so the unit
    -- is crore BDT for most rows but not guaranteed for all. The figure is
    -- stored exactly as published and never silently converted; a wrong
    -- currency assumption would corrupt every analysis built on it.
    value_crore         NUMERIC(18, 4),

    advertised_at       TIMESTAMPTZ,
    contract_signed_at  TIMESTAMPTZ,

    detail_url          TEXT,
    raw_document_id     BIGINT      REFERENCES raw_documents (id),
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One award per tender per lot per winner.
    UNIQUE (source_id, tender_external_ref, pkg_lot_id, winner)
);

-- "Who wins from this procuring entity" and "which entities have thin
-- competition" are both grouped lookups on these.
CREATE INDEX contract_awards_winner_idx ON contract_awards (winner);
CREATE INDEX contract_awards_entity_idx ON contract_awards (procuring_entity);
CREATE INDEX contract_awards_signed_idx ON contract_awards (contract_signed_at DESC);
CREATE INDEX contract_awards_tender_idx ON contract_awards (tender_external_ref);
CREATE INDEX contract_awards_district_idx ON contract_awards (district_name);
