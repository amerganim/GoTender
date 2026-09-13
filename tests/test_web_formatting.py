"""Display logic for the public directory.

These are pure functions on purpose, so the parts a contractor actually reads
-- how much time is left, how much money is at stake -- are testable without a
database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from tenderradar.web.app import countdown, dhaka, qs, taka, urgency
from tenderradar.web.queries import TenderFilters


def ahead(**kw) -> datetime:
    return datetime.now(UTC) + timedelta(**kw)


# ------------------------------------------------------------ timestamps


def test_utc_is_rendered_in_dhaka_time():
    """Stored UTC, displayed Asia/Dhaka (§9). Dhaka is UTC+6."""
    stamp = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    assert dhaka(stamp, "%d %b %Y, %H:%M") == "27 Sep 2026, 15:00"


def test_naive_timestamps_are_treated_as_utc():
    """A value that lost its tzinfo must not silently shift by six hours."""
    naive = datetime(2026, 9, 27, 9, 0)
    aware = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    assert dhaka(naive) == dhaka(aware)


def test_missing_timestamp_renders_a_dash():
    assert dhaka(None) == "—"


# ---------------------------------------------------------------- money


def test_taka_uses_lakh_grouping():
    """South Asian grouping: 8,00,000 not 800,000."""
    assert taka(Decimal("800000")) == "৳8,00,000"
    assert taka(Decimal("4000")) == "৳4,000"
    assert taka(Decimal("500")) == "৳500"


def test_taka_handles_crore():
    assert taka(Decimal("25500000")) == "৳2,55,00,000"


def test_missing_money_renders_a_dash():
    """e-GP publishes no estimated value, so this is the common case."""
    assert taka(None) == "—"


# ------------------------------------------------------------- deadlines


def test_countdown_reports_days_and_hours():
    assert countdown(ahead(days=9, hours=16)).startswith("9d 1")


def test_countdown_drops_to_hours_then_minutes():
    assert "h" in countdown(ahead(hours=5))
    assert countdown(ahead(minutes=40)).endswith("m left")


def test_countdown_marks_a_passed_deadline_closed():
    assert countdown(ahead(hours=-1)) == "closed"


def test_urgency_escalates_as_the_deadline_approaches():
    assert urgency(ahead(days=10)) == ""
    assert urgency(ahead(hours=48)) == "soon"
    assert urgency(ahead(hours=6)) == "urgent"
    assert urgency(ahead(hours=-1)) == "closed"


# -------------------------------------------------------- filter plumbing


def test_query_params_drop_empties_so_urls_stay_clean():
    filters = TenderFilters(district="Sylhet", nature=None, q="  ").normalized()
    assert filters.query_params() == {"district": "Sylhet"}


def test_query_params_override_one_facet_keeping_the_rest():
    filters = TenderFilters(district="Sylhet", nature="works").normalized()
    assert filters.query_params(nature="goods") == {
        "district": "Sylhet",
        "nature": "goods",
    }


def test_removing_a_facet_drops_it_from_the_url():
    filters = TenderFilters(district="Sylhet", nature="works").normalized()
    assert filters.query_params(district=None) == {"nature": "works"}


def test_blank_search_is_not_treated_as_a_filter():
    assert not TenderFilters(q="   ").normalized().is_filtered


def test_per_page_is_capped_so_a_url_cannot_request_everything():
    assert TenderFilters(per_page=10_000).normalized().per_page == 100
    assert TenderFilters(per_page=0).normalized().per_page == 1


def test_page_numbers_below_one_are_clamped():
    assert TenderFilters(page=-5).normalized().page == 1


def test_offset_follows_the_page():
    filters = TenderFilters(page=3, per_page=25).normalized()
    assert filters.offset == 50


def test_qs_filter_builds_a_querystring():
    assert qs({"district": "Sylhet", "nature": None, "page": 2}) in (
        "?district=Sylhet&page=2",
        "?page=2&district=Sylhet",
    )
    assert qs({}) == ""


def test_bangla_survives_a_querystring_round_trip():
    """Never assume ASCII in search or slugs (§9)."""
    from urllib.parse import parse_qs, urlparse

    bangla = "বৈদ্যুতিক"
    parsed = parse_qs(urlparse(qs({"q": bangla})).query)
    assert parsed["q"] == [bangla]
