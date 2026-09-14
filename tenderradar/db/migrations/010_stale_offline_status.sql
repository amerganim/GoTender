-- 010_stale_offline_status.sql
--
-- Repairs offline notices that had no closing date and therefore fell through
-- to status 'live' forever.
--
-- The offline adapter derived status from the closing date and defaulted to
-- live when there was none. On this feed that put 35 notices published between
-- 2004 and 2014 on the public site as current tenders, the oldest twelve years
-- stale. They also could never be matched, because Layer 1 filters on the
-- closing window and a NULL never satisfies it -- so they were simultaneously
-- visible to browsers and invisible to matching, which is the worst of both.
--
-- The adapter now decides by age when there is no closing date. This fixes
-- rows already stored, since re-crawling alone would not: the upsert merges
-- and a NULL closing date never overwrites anything.

UPDATE tenders t
   SET status = 'closed'
  FROM sources s
 WHERE s.id = t.source_id
   AND s.adapter_key = 'egp_offline'
   AND t.status = 'live'
   AND t.closing_at IS NULL
   AND (t.published_at IS NULL OR t.published_at < now() - interval '180 days');
