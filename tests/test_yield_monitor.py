"""Yield monitoring (§8.5).

"Monitor yield, not uptime. Scrapers do not crash -- they silently return zero."
Every case below is a way this project dies quietly if the check is wrong.
"""

from __future__ import annotations

from decimal import Decimal

from tenderradar.crawl.runner import CrawlRunner, RunReport


def report(
    items_found: int,
    baseline: str | None,
    status: str = "ok",
    is_full_sweep: bool = True,
) -> RunReport:
    return RunReport(
        source_key="egp_tender",
        items_found=items_found,
        baseline=Decimal(baseline) if baseline is not None else None,
        status=status,
        is_full_sweep=is_full_sweep,
    )


def test_healthy_run_is_not_flagged():
    assert not CrawlRunner._is_anomalous(report(3700, "3700"))


def test_zero_items_is_always_an_anomaly():
    """The live pool is never empty; zero means the parser or source broke."""
    assert CrawlRunner._is_anomalous(report(0, "3700"))


def test_zero_items_is_an_anomaly_even_with_no_history():
    """A brand new source that returns nothing is broken, not quiet."""
    assert CrawlRunner._is_anomalous(report(0, None))


def test_collapse_to_a_fraction_is_flagged():
    """The classic silent failure: markup changed, most rows stop parsing."""
    assert CrawlRunner._is_anomalous(report(400, "3700"))


def test_mild_variation_is_not_flagged():
    """Tender volume genuinely moves day to day; don't cry wolf."""
    assert not CrawlRunner._is_anomalous(report(3100, "3700"))
    assert not CrawlRunner._is_anomalous(report(2000, "3700"))


def test_boundary_is_half_the_baseline():
    assert not CrawlRunner._is_anomalous(report(1850, "3700"))  # exactly 50%
    assert CrawlRunner._is_anomalous(report(1849, "3700"))


def test_growth_is_never_an_anomaly():
    assert not CrawlRunner._is_anomalous(report(9000, "3700"))


def test_first_ever_run_has_no_baseline_and_is_accepted():
    assert not CrawlRunner._is_anomalous(report(3700, None))


def test_failed_run_is_always_an_anomaly():
    """Even if it scraped plenty before dying, a failed run must alert."""
    assert CrawlRunner._is_anomalous(report(3700, "3700", status="failed"))


def test_summary_names_the_anomaly():
    r = report(400, "3700")
    r.yield_anomaly = CrawlRunner._is_anomalous(r)
    assert "YIELD ANOMALY" in r.summary()


# ------------------------------------------- partial runs are not a yardstick


def test_capped_dev_run_is_not_judged_against_a_full_sweep_baseline():
    """`crawl --pages 2` finds ~200 where a full sweep finds ~3,800.

    Without this, every dev run against a healthy baseline would scream a
    yield anomaly, and operators would learn to ignore the one alert that
    actually matters.
    """
    assert not CrawlRunner._is_anomalous(
        report(200, "3700", is_full_sweep=False)
    )


def test_a_partial_run_that_finds_nothing_is_still_an_anomaly():
    """Capped or not, zero items means something is broken."""
    assert CrawlRunner._is_anomalous(report(0, "3700", is_full_sweep=False))


def test_a_failed_partial_run_is_still_an_anomaly():
    assert CrawlRunner._is_anomalous(
        report(200, "3700", status="failed", is_full_sweep=False)
    )


def test_full_sweep_collapse_is_still_caught():
    """The guard must not have been weakened for real sweeps."""
    assert CrawlRunner._is_anomalous(report(400, "3700", is_full_sweep=True))


def test_summary_marks_a_partial_run():
    assert "partial (not a baseline)" in report(
        200, None, is_full_sweep=False
    ).summary()
    assert "partial" not in report(3700, None, is_full_sweep=True).summary()
