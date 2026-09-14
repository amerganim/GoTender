"""Offline tender adapter.

Offline notices are the missing half of Gate 0's coverage target. Their feed
differs from the e-tender one in four ways that each cause silent data loss if
missed, so every one has a test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tenderradar.adapters.base import FetchResult, PayloadKind
from tenderradar.adapters.egp_offline import (
    STALE_WITHOUT_CLOSING,
    EgpOfflineTenderAdapter,
    parse_closing_date,
)
from tenderradar.models import ProcurementNature, TenderStatus

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def adapter() -> EgpOfflineTenderAdapter:
    return EgpOfflineTenderAdapter()


@pytest.fixture
def records(adapter: EgpOfflineTenderAdapter):
    return adapter.parse(
        FetchResult(
            url="https://www.eprocure.gov.bd/TenderDashboardOfflineServlet",
            content=(FIXTURES / "egp_offline_list.html").read_bytes(),
            kind=PayloadKind.LIST,
        )
    )


def test_every_row_parses(records):
    assert len(records) == 20
    assert len({r.external_ref for r in records}) == 20


def test_core_fields(records):
    first = records[0]
    assert first.external_ref == "888"
    assert first.reference_no == "33.02.2200.000.400.11.0004.26.325"
    assert first.procurement_nature is ProcurementNature.GOODS
    assert first.procurement_type == "NCT"
    assert first.procurement_method == "RFQ"
    assert first.description


# ------------------------------------------ trap 1: the third line is a person


def test_contact_person_is_never_filed_as_an_organization(records):
    """Rows read [ministry, department, "MD NAZMUL HUDA"].

    The e-tender adapter right-aligns four hierarchy levels; doing that here
    would file a human being as the procuring entity and show their name
    wherever the buyer belongs.
    """
    first = records[0]
    assert first.organization.ministry == "Ministry of Fisheries & Livestock"
    assert first.organization.agency == "Department of Fisheries"
    assert first.organization.procuring_entity is None
    assert first.organization.deepest == "Department of Fisheries"

    for record in records:
        assert "NAZMUL HUDA" not in (record.organization.deepest or "")


def test_two_line_hierarchy_is_handled():
    org = EgpOfflineTenderAdapter._organization_from_parts(
        ["Power Division", "RPCL-NORINCO INTL POWER LIMITED", "Md Adnan Ibrahim"]
    )
    assert org.ministry == "Power Division"
    assert org.agency == "RPCL-NORINCO INTL POWER LIMITED"
    assert org.deepest == "RPCL-NORINCO INTL POWER LIMITED"


# ------------------------------------------------ trap 2: dates carry no time


def test_date_only_closing_becomes_end_of_day():
    """"14-Sep-2026" parsed naively is 00:00, the START of the closing day.

    Layer 1 filters on closing_at > now(), so a tender would disappear from
    matching a day early and nobody would ever report it -- they simply would
    not hear about a tender they could have bid on.
    """
    closing = parse_closing_date("14-Sep-2026")
    assert closing is not None
    # 23:59 Dhaka on the 14th is 17:59 UTC on the 14th.
    assert closing == datetime(2026, 9, 14, 17, 59, tzinfo=UTC)


def test_a_supplied_time_is_left_alone():
    """Only date-only values get shifted; a real timestamp is authoritative."""
    assert parse_closing_date("14-Sep-2026 15:00") == datetime(
        2026, 9, 14, 9, 0, tzinfo=UTC
    )


def test_closing_on_the_stated_day_is_still_live(records):
    """The fixture's tender 888 closes 14-Sep. On 14-Sep it must not be closed."""
    record = next(r for r in records if r.external_ref == "888")
    assert record.closing_at == datetime(2026, 9, 14, 17, 59, tzinfo=UTC)


def test_junk_dates_do_not_raise():
    assert parse_closing_date(None) is None
    assert parse_closing_date("") is None
    assert parse_closing_date("not a date") is None


# --------------------------------- trap 3: the feed includes closed notices


def test_status_is_derived_not_assumed(records):
    """Unlike the e-tender search, this feed is not filtered to Live."""
    statuses = {r.status for r in records}
    assert TenderStatus.CLOSED in statuses
    assert TenderStatus.LIVE in statuses


def test_past_closing_dates_are_marked_closed(records):
    now = datetime.now(UTC)
    for record in records:
        if record.closing_at and record.closing_at <= now:
            assert record.status is TenderStatus.CLOSED


# ------------------------------------------- trap 4: opaque detail links


def test_detail_url_carries_the_opaque_hash(records):
    """There is no id-addressable detail page; the hash is the only way back."""
    first = records[0]
    assert first.detail_url is not None
    assert "ViewTenderWithoutPQ.jsp?p=" in first.detail_url
    assert first.detail_url.startswith("https://www.eprocure.gov.bd/")


# ----------------------------------------------------- shared-shape traps


def test_bare_tr_fragment_is_wrapped(adapter):
    """Same as the e-tender feed: HTML5 parsers discard rows outside a table."""
    raw = (FIXTURES / "egp_offline_list.html").read_bytes()
    assert b"<table" not in raw[:200].lower()
    assert len(adapter.parse(FetchResult(url="x", content=raw, kind=PayloadKind.LIST))) == 20


def test_session_expired_yields_nothing(adapter):
    result = FetchResult(
        url="x",
        content=b"<html><title>Session Expired</title><tr><td>1</td></tr></html>",
        kind=PayloadKind.LIST,
    )
    assert adapter.parse(result) == []


def test_parse_is_pure(adapter, records):
    again = adapter.parse(
        FetchResult(
            url="x",
            content=(FIXTURES / "egp_offline_list.html").read_bytes(),
            kind=PayloadKind.LIST,
        )
    )
    assert [r.canonical_hash() for r in records] == [r.canonical_hash() for r in again]


def test_adapter_is_registered():
    from tenderradar.adapters import registry

    assert "egp_offline" in registry


# ------------------- trap 5: a missing closing date must not mean "live"


def test_a_notice_with_no_closing_date_does_not_stay_live_forever():
    """The bug this replaced put 2004-2014 notices on the site as current.

    Status used to fall through to Live whenever there was no closing date, so
    a notice with no stated deadline stayed live indefinitely. Those rows were
    also unmatchable, because Layer 1 filters on the closing window and NULL
    never satisfies it -- visible to browsers, invisible to matching, which is
    the worst of both.
    """
    ancient = datetime.now(UTC) - timedelta(days=4400)  # a 2014 notice
    assert (
        EgpOfflineTenderAdapter._derive_status(ancient, None) is TenderStatus.CLOSED
    )


def test_a_recent_notice_with_no_closing_date_is_still_shown():
    """Unknown is not the same as expired.

    A notice published last week with no stated deadline might genuinely be
    open, so it stays Live and a browsing contractor can judge for themselves.
    It still will not be alerted on -- we will show a deadline we do not know,
    but we will not promise one.
    """
    recent = datetime.now(UTC) - timedelta(days=7)
    assert EgpOfflineTenderAdapter._derive_status(recent, None) is TenderStatus.LIVE


def test_the_staleness_boundary():
    now = datetime.now(UTC)
    inside = now - (STALE_WITHOUT_CLOSING - timedelta(days=1))
    outside = now - (STALE_WITHOUT_CLOSING + timedelta(days=1))
    assert EgpOfflineTenderAdapter._derive_status(inside, None) is TenderStatus.LIVE
    assert EgpOfflineTenderAdapter._derive_status(outside, None) is TenderStatus.CLOSED


def test_staleness_window_cannot_hide_a_biddable_tender():
    """Layer 1 never looks beyond 90 days, so 180 is safely generous."""
    assert STALE_WITHOUT_CLOSING > timedelta(days=90)


def test_a_row_with_no_dates_at_all_is_not_live():
    """No closing date and no publication date is not evidence of a live
    tender; it is evidence of a row we cannot date."""
    assert EgpOfflineTenderAdapter._derive_status(None, None) is TenderStatus.CLOSED


def test_an_explicit_closing_date_still_decides():
    now = datetime.now(UTC)
    assert EgpOfflineTenderAdapter._derive_status(
        now - timedelta(days=4400), now + timedelta(days=7)
    ) is TenderStatus.LIVE
    assert EgpOfflineTenderAdapter._derive_status(
        now - timedelta(days=1), now - timedelta(hours=1)
    ) is TenderStatus.CLOSED
