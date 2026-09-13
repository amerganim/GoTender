-- 003_full_sweep_flag.sql
--
-- Yield monitoring (section 8.5) compares a run's item count against that
-- source's trailing average. That comparison is only meaningful between runs
-- of the same shape. A capped dev run (--pages 2, ~200 items) and a full sweep
-- (~3,800 items) differ by a factor of twenty, so mixing them makes the
-- average meaningless in both directions:
--
--   * a baseline dragged down by dev runs hides a real collapse in coverage
--   * a baseline raised by full sweeps makes every later dev run trip a false
--     anomaly
--
-- Full sweeps are now flagged, and only they feed the trailing average.

ALTER TABLE crawl_runs
    ADD COLUMN is_full_sweep BOOLEAN NOT NULL DEFAULT TRUE;

-- Every run recorded before this column existed was either a page-capped dev
-- run or was still in flight, so none of them is a trustworthy baseline.
UPDATE crawl_runs SET is_full_sweep = FALSE;

-- Partial runs are still kept: they are real crawl history and their parse
-- failures and errors still matter. They are simply not a yardstick.
CREATE INDEX crawl_runs_baseline_idx
    ON crawl_runs (source_id, started_at DESC)
    WHERE is_full_sweep AND status = 'ok';
