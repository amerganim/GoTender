"""Fixture HTML -> expected records (§9). Fixtures are real portal responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tenderradar.adapters.base import FetchResult, PayloadKind
from tenderradar.adapters.egp import (
    EgpTenderAdapter,
    _method_code,
    parse_dhaka_datetime,
    parse_money,
)
from tenderradar.models import ProcurementNature, TenderStatus

FIXTURES = Path(__file__).parent / "fixtures"


def _result(name: str, kind: PayloadKind, **meta: object) -> FetchResult:
    return FetchResult(
        url="https://www.eprocure.gov.bd/test",
        content=(FIXTURES / name).read_bytes(),
        kind=kind,
        meta=meta,
    )


@pytest.fixture
def adapter() -> EgpTenderAdapter:
    return EgpTenderAdapter()


# ------------------------------------------------------------------ helpers


def test_dhaka_datetime_converts_to_utc():
    # Dhaka is UTC+6 year round; 13:00 local is 07:00 UTC.
    assert parse_dhaka_datetime("13-Sep-2026 13:00") == datetime(
        2026, 9, 13, 7, 0, tzinfo=UTC
    )


def test_dhaka_datetime_rejects_junk():
    assert parse_dhaka_datetime("") is None
    assert parse_dhaka_datetime("not a date") is None
    assert parse_dhaka_datetime(None) is None


def test_parse_money_handles_separators_and_noise():
    assert parse_money("800000") == Decimal("800000")
    assert parse_money("8,00,000") == Decimal("800000")  # lakh-style grouping
    assert parse_money("BDT 4,000.50") == Decimal("4000.50")
    assert parse_money("0") is None
    assert parse_money("") is None


def test_method_code_extracts_abbreviation():
    assert _method_code("Open Tendering Method (OTM)") == "OTM"
    assert _method_code("Limited Tendering Method (LTM)") == "LTM"
    # Unrecognized shapes survive intact rather than becoming None.
    assert _method_code("Direct Procurement Method") == "Direct Procurement Method"


# --------------------------------------------------------------- list page


def test_list_fragment_parses_every_row(adapter: EgpTenderAdapter):
    """The servlet returns bare <tr> with no <table>; parsing must still work.

    An HTML5 parser silently discards rows outside a table, which would make
    this adapter return zero rows forever without raising anything.
    """
    records = adapter.parse(_result("egp_list_live.html", PayloadKind.LIST))
    assert len(records) == 10
    assert len({r.external_ref for r in records}) == 10


def test_list_row_fields(adapter: EgpTenderAdapter):
    record = adapter.parse(_result("egp_list_live.html", PayloadKind.LIST))[0]

    assert record.external_ref == "1330849"
    assert record.package_no == "PSWSC-6145"
    assert record.reference_no.startswith("46.03.9100.061.07.141.17-421")
    assert record.procurement_nature is ProcurementNature.WORKS
    assert record.procurement_type == "NCT"
    assert record.procurement_method == "OTM"
    assert record.status is TenderStatus.LIVE
    assert record.published_at == datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
    assert record.closing_at == datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    assert "Deep Tube well" in record.description


def test_list_row_keeps_commas_inside_organization_names(adapter: EgpTenderAdapter):
    """Hierarchy is split on <br>, not on commas.

    "Ministry of Local Government, Rural Development and Co-operatives" would
    be shredded into three fake levels by a comma split.
    """
    record = adapter.parse(_result("egp_list_live.html", PayloadKind.LIST))[0]
    org = record.organization

    assert org.ministry == (
        "Ministry of Local Government, Rural Development and Co-operatives"
    )
    assert org.division == "Local Government Division"
    assert org.agency == "Department of Public Health Engineering (DPHE)"
    assert org.procuring_entity == "Office of the Executive Engineer DPHE, Sylhet"
    assert org.deepest == "Office of the Executive Engineer DPHE, Sylhet"


def test_short_hierarchy_is_right_aligned():
    """Two lines mean agency + entity, not ministry + division."""
    org = EgpTenderAdapter._organization_from_parts(["Some Agency", "Some Office"])
    assert org.ministry is None
    assert org.division is None
    assert org.agency == "Some Agency"
    assert org.procuring_entity == "Some Office"


def test_session_expired_fragment_yields_nothing(adapter: EgpTenderAdapter):
    """The portal answers 200 with a session page; status codes can't be trusted."""
    result = FetchResult(
        url="x",
        content=b"<html><title>Session Expired</title><tr><td>1</td></tr></html>",
        kind=PayloadKind.LIST,
    )
    assert adapter.parse(result) == []


# ------------------------------------------------------------- detail page


@pytest.fixture
def detail(adapter: EgpTenderAdapter):
    records = adapter.parse(
        _result("egp_detail_1330849.html", PayloadKind.DETAIL, tender_id="1330849")
    )
    assert len(records) == 1
    return records[0]


def test_detail_identity_and_location(detail):
    assert detail.external_ref == "1330849"
    assert detail.district == "Sylhet"
    assert detail.upazila == "Golapganj and Beanibazar Upazila"
    assert detail.app_id == "230146"  # links the tender to its APP entry
    assert detail.project_name.startswith("Project for Safe Water Supply")


def test_detail_money_fields(detail):
    # e-GP publishes no estimated cost; security is the value-range proxy.
    assert detail.estimated_value is None
    assert detail.tender_security == Decimal("800000")
    assert detail.document_price == Decimal("4000")


def test_detail_all_four_timestamps(detail):
    assert detail.published_at == datetime(2026, 9, 13, 7, 0, tzinfo=UTC)
    assert detail.closing_at == datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    assert detail.opening_at == datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    assert detail.document_last_selling_at == datetime(2026, 9, 27, 7, 0, tzinfo=UTC)


def test_detail_categories_split(detail):
    assert len(detail.categories) > 10
    assert "Construction work" in detail.categories
    assert all(c == c.strip() and c for c in detail.categories)


def test_detail_lots(detail):
    assert len(detail.lots) == 1
    lot = detail.lots[0]
    assert lot.lot_no == "1"
    assert lot.location == "Golapganj and Beanibazar Upazila"
    assert lot.security_amount == Decimal("800000")
    assert lot.start_date is not None and lot.completion_date is not None
    assert lot.completion_date > lot.start_date


def test_detail_carries_eligibility_prose(detail):
    """Phase 5 Layer 3 input, available without opening a PDF."""
    assert detail.eligibility_text
    assert len(detail.eligibility_text) > 500
    text = detail.eligibility_text.lower()
    assert "trade license" in text
    assert "turnover" in text


def test_detail_labels_tolerate_whitespace_variation(adapter: EgpTenderAdapter):
    """Labels break across <br> and padding; lookup ignores whitespace."""
    assert adapter._first_present(
        {"tender/proposal closing date and time": "13-Sep-2026 13:00"},
        "tender/proposalclosingdate and time",
    ) == "13-Sep-2026 13:00"


def test_fixtures_are_uncorrupted_captures():
    """Fixtures must be the portal's exact bytes.

    Capturing a response through text mode on Windows rewrites the \\n of each
    existing \\r\\n, producing \\r\\r\\n and a fixture that no longer matches
    what the parser sees in production. Write captures with 'wb'.
    """
    for path in FIXTURES.glob("egp_*.html"):
        content = path.read_bytes()
        assert b"\r\r\n" not in content, f"{path.name} has doubled carriage returns"
        assert b"\x00" not in content, f"{path.name} contains NUL bytes"


def test_parse_is_pure(adapter: EgpTenderAdapter):
    """Same bytes in, same records out -- what makes archive replay possible."""
    first = adapter.parse(_result("egp_list_live.html", PayloadKind.LIST))
    second = adapter.parse(_result("egp_list_live.html", PayloadKind.LIST))
    assert [r.canonical_hash() for r in first] == [r.canonical_hash() for r in second]


# ------------------------------------------------ package number detection


def test_package_number_split_on_real_shapes():
    """Most live rows carry no package number; line 1 is then the description.

    Treating line 1 as the package number unconditionally shifted every field
    by one for ~79% of the live pool.
    """
    split = EgpTenderAdapter._split_brief

    # Real package numbers: one token, digits or slashes.
    assert split(["PSWSC-6145", "Installation of tube wells"]) == (
        "PSWSC-6145",
        "Installation of tube wells",
    )
    assert split(["EED/DM/Development/2026-27/PG-04", "Supply"]) == (
        "EED/DM/Development/2026-27/PG-04",
        "Supply",
    )
    assert split(["Police/26-27/Thana/WD25a", "Construction"]) == (
        "Police/26-27/Thana/WD25a",
        "Construction",
    )

    # Prose is a description, never a package number.
    assert split(["Procurement of Surgical Equipment"]) == (
        None,
        "Procurement of Surgical Equipment",
    )
    assert split(["Call Center Helpdesk Service for NESCO", "and more"]) == (
        None,
        "Call Center Helpdesk Service for NESCO and more",
    )
    assert split([]) == (None, None)


def test_list_row_without_package_number_keeps_description_in_place(
    adapter: EgpTenderAdapter,
):
    for record in adapter.parse(_result("egp_list_live.html", PayloadKind.LIST)):
        if record.package_no is not None:
            # A package number never contains whitespace on this source.
            assert " " not in record.package_no, record.package_no
        # The description must never be left empty while a package number
        # holds the prose that belongs in it.
        assert record.description, record.external_ref
