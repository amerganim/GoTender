-- 011_remove_portal_test_record.sql
--
-- Removes e-GP's own training record, which the portal publishes inside its
-- Live tender feed. It is self-described as invalid and used to demonstrate
-- the corrigendum process, and it carries a closing date years in the future,
-- so no ordinary status or date filter catches it.
--
-- It was crawled faithfully -- this is the portal's data, not a parser bug --
-- but a contractor who sees "This is an Invalid Tender" among their results
-- reasonably concludes the whole service is unreliable.
--
-- Deleted rather than marked closed: it was never a tender, so leaving it in
-- the corpus would skew live counts, award joins and any future analytics, for
-- no benefit. The raw archive still holds the bytes exactly as fetched (§8.4),
-- so nothing is actually lost and the decision is reversible.
--
-- The adapters now skip it at parse time and log each exclusion by reference,
-- so this cannot silently return.
--
-- Matching is on the portal's own wording, and only on phrases no genuine
-- procurement notice would contain -- verified against realistic near misses
-- such as "invalid tender security" and "treated as invalid".

DELETE FROM tenders
 WHERE lower(coalesce(description, '') || ' ' || coalesce(title, ''))
       LIKE '%this is an invalid tender%'
    OR lower(coalesce(description, '') || ' ' || coalesce(title, ''))
       LIKE '%used to explain corrigendum process%';
