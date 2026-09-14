"""Contract award parsing.

Awards are the one dataset in this project that cannot be backfilled from the
future, so a parsing bug found later cannot be repaired by re-reading the
source as it was. The raw archive is the safety net; these tests are the alarm.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tenderradar.adapters.base import FetchResult, PayloadKind
from tenderradar.awards.collector import parse_awards, parse_value_crore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def records():
    return parse_awards(
        FetchResult(
            url="https://www.eprocure.gov.bd/SearchNoaServlet",
            content=(FIXTURES / "egp_awards_list.html").read_bytes(),
            kind=PayloadKind.LIST,
        )
    )


def test_every_row_parses(records):
    assert len(records) == 20


def test_every_award_has_a_winner(records):
    """The winner is the column this table exists for; a blank one is useless."""
    assert all(r.winner and r.winner.strip() for r in records)


def test_core_fields(records):
    first = records[0]
    assert first.winner == "M/S. Patowary Construction"
    assert first.tender_external_ref == "1328535"
    assert first.pkg_lot_id == "2160191"
    assert first.district_name == "Bandarban"
    assert first.procurement_method == "RFQ"
    assert first.procuring_entity == "Upazila Fisheries office, Rowangchhari"
    assert first.value_crore == Decimal("0.010")
    assert first.contract_signed_at == datetime(2026, 9, 12, 18, 0, tzinfo=UTC)


def test_awards_link_back_to_tender_ids(records):
    """The join key to the tenders we crawl; without it this is just a list."""
    assert all(r.tender_external_ref for r in records)
    assert all(r.tender_external_ref.isdigit() for r in records)


def test_detail_url_is_reconstructed(records):
    url = records[0].detail_url
    assert url and "ViewAwardedContracts.jsp" in url
    assert "pkgLotId=2160191" in url and "tenderid=1328535" in url


# ------------------------------------------------------------------ value


def test_value_is_stored_exactly_as_published():
    """Labelled "Value (Cr. BDT) /(Other Currency)".

    Converting to BDT here would bake a currency assumption into every row.
    Most are crore BDT; the portal does not promise all of them are, and a
    wrong multiplier would corrupt every analysis built on this column.
    """
    assert parse_value_crore("0.010") == Decimal("0.010")
    assert parse_value_crore("18.706") == Decimal("18.706")
    assert parse_value_crore("1,234.5") == Decimal("1234.5")


def test_missing_or_junk_value_is_none_not_zero():
    """Zero would mean a free contract; unknown must stay unknown."""
    assert parse_value_crore(None) is None
    assert parse_value_crore("") is None
    assert parse_value_crore("N/A") is None


def test_small_values_keep_their_precision(records):
    """Most awards are fractions of a crore; rounding would flatten them."""
    small = [r.value_crore for r in records if r.value_crore and r.value_crore < 1]
    assert small
    assert any(v < Decimal("0.05") for v in small)


# ------------------------------------------------------------ robustness


def test_bare_tr_fragment_is_wrapped():
    raw = (FIXTURES / "egp_awards_list.html").read_bytes()
    assert b"<table" not in raw[:200].lower()
    assert len(parse_awards(FetchResult(url="x", content=raw, kind=PayloadKind.LIST))) == 20


def test_session_expired_yields_nothing():
    result = FetchResult(
        url="x",
        content=b"<html><title>Session Expired</title><tr><td>x</td></tr></html>",
        kind=PayloadKind.LIST,
    )
    assert parse_awards(result) == []


def test_parse_is_pure(records):
    again = parse_awards(
        FetchResult(
            url="x",
            content=(FIXTURES / "egp_awards_list.html").read_bytes(),
            kind=PayloadKind.LIST,
        )
    )
    assert [r.winner for r in records] == [r.winner for r in again]
    assert [r.value_crore for r in records] == [r.value_crore for r in again]


def test_fixture_is_an_uncorrupted_capture():
    content = (FIXTURES / "egp_awards_list.html").read_bytes()
    assert b"\r\r\n" not in content
    assert b"\x00" not in content
