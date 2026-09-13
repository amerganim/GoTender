-- 005_enrichment_change_type.sql
--
-- Separates "we learned more" from "the procuring entity amended the notice".
--
-- A tender is first seen in a list sweep, which carries a subset of fields.
-- Its detail page arrives later and fills in district, security, document
-- price and opening date. Every one of those changes was NULL -> value, but
-- they were all recorded as corrigenda, so the public amendment history told
-- contractors the notice had been amended when nothing had changed at source.
--
-- On this corpus that was 300 of 385 recorded corrigenda: 78% noise. In Phase
-- 2 it would mean alerting every subscriber about amendments that never
-- happened, which is precisely the irrelevant-alert problem we exist to fix.

ALTER TABLE tender_versions
    DROP CONSTRAINT tender_versions_change_type_check;

ALTER TABLE tender_versions
    ADD CONSTRAINT tender_versions_change_type_check
    CHECK (change_type IN
           ('new', 'corrigendum', 'cancellation', 'extension', 'enrichment'));

-- Reclassify history: a change in which every field went from nothing to
-- something is enrichment, whatever it was labelled at the time.
UPDATE tender_versions
   SET change_type = 'enrichment'
 WHERE change_type = 'corrigendum'
   AND changed_fields <> '{}'::jsonb
   AND NOT EXISTS (
       SELECT 1
         FROM jsonb_each(changed_fields) AS f(key, val)
        WHERE f.val->>'old' IS NOT NULL
   );

-- The public amendment history reads this constantly.
CREATE INDEX tender_versions_amendments_idx
    ON tender_versions (tender_id, version_no DESC)
    WHERE change_type IN ('corrigendum', 'cancellation', 'extension');
