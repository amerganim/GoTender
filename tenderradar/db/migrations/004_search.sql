-- 004_search.sql — full-text search for the Phase 1 public directory.
--
-- Uses the 'simple' text search configuration deliberately, NOT 'english'.
-- English stemming would mangle Bangla, and tender text is mixed Bangla and
-- English in the same field (§9). 'simple' lowercases and tokenizes without
-- stemming or a stopword list, which is the only configuration that treats
-- both scripts sanely.
--
-- A generated column keeps the vector in lockstep with the row; nothing in the
-- crawler needs to remember to maintain it.

ALTER TABLE tenders
    ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            coalesce(title, '') || ' ' ||
            coalesce(description, '') || ' ' ||
            coalesce(package_no, '') || ' ' ||
            coalesce(reference_no, '') || ' ' ||
            coalesce(organization_path, '') || ' ' ||
            coalesce(district_name, '') || ' ' ||
            coalesce(project_name, '')
        )
    ) STORED;

CREATE INDEX tenders_search_idx ON tenders USING GIN (search_vector);

-- Trigram index for substring matches the tokenizer cannot serve: a user
-- pasting a partial package number ("PSWSC-61") or a fragment of a reference.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX tenders_package_trgm_idx
    ON tenders USING GIN (package_no gin_trgm_ops);
CREATE INDEX tenders_reference_trgm_idx
    ON tenders USING GIN (reference_no gin_trgm_ops);

-- Browse facets: the directory lists by organization and district constantly.
CREATE INDEX tenders_org_path_idx
    ON tenders (organization_path) WHERE status = 'live';
