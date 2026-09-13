"""Dedup, hashing and the list/detail merge rule (§6, §9).

The merge behaviour is the one that silently ruins a corpus: get it wrong and
every 30-minute sweep invents a corrigendum, so every user gets alerted about
the same tender forever and the feedback signal dies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tenderradar.db.repo import merge_records
from tenderradar.models import (
    ChangeType,
    OrganizationRef,
    ProcurementNature,
    TenderRecord,
    TenderStatus,
    normalize_text,
)


def list_record(**overrides) -> TenderRecord:
    """What a list-page row gives us: no district, no money, no eligibility."""
    base = dict(
        source_key="egp_tender",
        external_ref="1330849",
        title="Installation of deep tube wells",
        package_no="PSWSC-6145",
        description="Installation of deep tube wells",
        organization=OrganizationRef(procuring_entity="DPHE Sylhet"),
        procurement_nature=ProcurementNature.WORKS,
        procurement_method="OTM",
        published_at=datetime(2026, 9, 13, 7, 0, tzinfo=UTC),
        closing_at=datetime(2026, 9, 27, 9, 0, tzinfo=UTC),
        status=TenderStatus.LIVE,
    )
    return TenderRecord(**(base | overrides))


def detail_record(**overrides) -> TenderRecord:
    """What a detail page adds on top. Overrides win over the defaults."""
    base = dict(
        district="Sylhet",
        upazila="Golapganj",
        tender_security=Decimal("800000"),
        document_price=Decimal("4000"),
        eligibility_text="Trade licence, VAT, turnover certificate required.",
        categories=["Construction work"],
    )
    return list_record(**(base | overrides))


# --------------------------------------------------------------- hashing


def test_hash_ignores_whitespace_and_unicode_form():
    a = list_record(title="Deep   Tube  well")
    b = list_record(title="Deep Tube well")
    assert a.canonical_hash() == b.canonical_hash()


def test_hash_ignores_fields_that_are_crawl_noise():
    """detail_url and raw_document_id change per crawl; they must not version."""
    a = list_record()
    b = list_record()
    b = b.model_copy(update={"detail_url": "https://other", "raw_document_id": 99})
    assert a.canonical_hash() == b.canonical_hash()


def test_hash_changes_when_closing_date_moves():
    a = list_record()
    b = list_record(closing_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC))
    assert a.canonical_hash() != b.canonical_hash()


# ----------------------------------------------------------------- merge


def test_detail_enriches_a_stored_list_record():
    stored, incoming = list_record(), detail_record()
    merged = merge_records(stored, incoming)

    assert merged.district == "Sylhet"
    assert merged.tender_security == Decimal("800000")
    assert merged.eligibility_text


def test_list_sweep_never_erases_detail_fields():
    """The regression that would fake a corrigendum every 30 minutes."""
    stored = detail_record()
    merged = merge_records(stored, list_record())

    assert merged.district == "Sylhet"
    assert merged.upazila == "Golapganj"
    assert merged.tender_security == Decimal("800000")
    assert merged.document_price == Decimal("4000")
    assert merged.eligibility_text
    assert merged.categories == ["Construction work"]


def test_repeated_list_sweeps_are_hash_stable_against_a_detail_record():
    """Three sweeps in a row must produce no version churn at all."""
    stored = detail_record()
    for _ in range(3):
        merged = merge_records(stored, list_record())
        assert merged.canonical_hash() == stored.canonical_hash()
        stored = merged


def test_empty_values_do_not_overwrite():
    stored = detail_record()
    merged = merge_records(stored, list_record(title="", categories=[]))
    assert merged.title == stored.title
    assert merged.categories == ["Construction work"]


def test_real_change_still_survives_the_merge():
    stored = detail_record()
    incoming = list_record(closing_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC))
    merged = merge_records(stored, incoming)

    assert merged.canonical_hash() != stored.canonical_hash()
    assert merged.district == "Sylhet"  # enrichment preserved through the change


# ----------------------------------------------------------- change types


def test_later_closing_date_is_an_extension():
    stored = detail_record()
    merged = merge_records(
        stored, list_record(closing_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC))
    )
    assert merged.infer_change_type(stored) is ChangeType.EXTENSION


def test_cancelled_status_is_a_cancellation():
    stored = detail_record()
    merged = merge_records(stored, list_record(status=TenderStatus.CANCELLED))
    assert merged.infer_change_type(stored) is ChangeType.CANCELLATION


def test_other_edits_are_corrigenda():
    stored = detail_record()
    merged = merge_records(stored, list_record(description="Revised scope of works"))
    assert merged.infer_change_type(stored) is ChangeType.CORRIGENDUM


def test_changed_fields_reports_old_and_new():
    stored = detail_record()
    merged = merge_records(stored, list_record(procurement_method="LTM"))
    diff = merged.changed_fields(stored)

    assert diff["procurement_method"] == {"old": "OTM", "new": "LTM"}
    assert "district" not in diff


# ------------------------------------------------------- Bangla handling


def test_bangla_unicode_forms_normalize_to_one_hash():
    """The same grapheme in two encodings must not look like two tenders.

    U+09A1 U+09BC (da + nukta) and U+09DC (dda) render identically, and real
    e-GP text mixes both. Without NFC normalization the same tender would hash
    differently between crawls and churn versions forever. Written as escapes
    on purpose: as literals these two lines are indistinguishable on screen.
    """
    decomposed = "ড়াক"   # da + nukta + aa + ka
    composed = "ড়াক"            # dda + aa + ka

    assert decomposed != composed                # different codepoints in
    assert len(decomposed) == len(composed) + 1

    assert (
        list_record(title=decomposed).canonical_hash()
        == list_record(title=composed).canonical_hash()
    )


def test_normalize_text_collapses_and_strips():
    kaj = "কাজ"          # "kaj"
    boro_decomposed = "বড়"  # "boro", da + nukta
    boro_composed = "বড়"           # "boro", precomposed dda

    # Whitespace: padding stripped, internal runs collapsed to one space.
    assert normalize_text(f"  {kaj}   {boro_decomposed}  ") == (
        f"{kaj} {boro_decomposed}"
    )

    # Both encodings land on ONE form, which is what the hash depends on.
    # Note it is the DEcomposed form: U+09DC is on Unicode's composition
    # exclusion list, so NFC pulls it apart rather than fusing it. Either
    # direction is fine for us; agreeing on one is the requirement.
    assert normalize_text(boro_composed) == normalize_text(boro_decomposed)

    assert normalize_text("   ") is None
    assert normalize_text(None) is None


# --------------------------------------- detail outranks list (provenance)


def test_list_cannot_overwrite_a_field_the_detail_page_set():
    """The bug that produced a corrigendum per tender per sweep.

    The two views genuinely disagree: the list renders a title in one case,
    the detail page in another, and the list omits the package number for most
    tenders. Skipping None is not enough -- a list sweep must not contradict
    detail data at all.
    """
    stored = detail_record(package_no="Police/26-27/Thana/WD25a",
                           title="Procurement of Surgical Equipment")
    incoming = list_record(package_no=None,
                           title="Procurement of surgical equipment")

    merged = merge_records(stored, incoming, fill_only=True)

    assert merged.package_no == "Police/26-27/Thana/WD25a"
    assert merged.title == "Procurement of Surgical Equipment"
    assert merged.canonical_hash() == stored.canonical_hash()


def test_fill_only_still_populates_genuinely_empty_fields():
    stored = list_record(district=None, tender_security=None)
    incoming = list_record(district="Sylhet", tender_security=Decimal("800000"))

    merged = merge_records(stored, incoming, fill_only=True)

    assert merged.district == "Sylhet"
    assert merged.tender_security == Decimal("800000")


def test_detail_sweep_may_still_correct_list_data():
    """fill_only applies only to list sweeps; detail keeps its authority."""
    stored = list_record(title="Procurement of surgical equipment")
    incoming = detail_record(title="Procurement of Surgical Equipment")

    merged = merge_records(stored, incoming, fill_only=False)

    assert merged.title == "Procurement of Surgical Equipment"


def test_repeated_list_sweeps_after_detail_are_completely_stable():
    """Ten sweeps, zero version churn -- the 30-minute crawl running all day."""
    stored = detail_record(package_no="PSWSC-6145")
    for _ in range(10):
        merged = merge_records(
            stored, list_record(package_no=None, title="different CASE here"),
            fill_only=True,
        )
        assert merged.canonical_hash() == stored.canonical_hash()
        stored = merged


# ------------------------------------------- money round-trips through SQL


def test_decimal_scale_from_postgres_does_not_change_the_hash():
    """NUMERIC(18,2) hands back 800000.00 where the parser produced 800000.

    The two are equal as Decimals, so a field diff shows nothing, but str()
    renders them differently. An unquantized hash therefore flipped on every
    reload and produced a version with an EMPTY changed_fields -- a phantom
    corrigendum for every tender carrying a money value.
    """
    parsed = detail_record(tender_security=Decimal("800000"),
                           document_price=Decimal("4000"))
    from_db = detail_record(tender_security=Decimal("800000.00"),
                            document_price=Decimal("4000.00"))

    assert parsed.canonical_hash() == from_db.canonical_hash()
    assert parsed.changed_fields(from_db) == {}


def test_real_money_differences_are_still_detected():
    a = detail_record(tender_security=Decimal("800000.00"))
    b = detail_record(tender_security=Decimal("800000.01"))
    assert a.canonical_hash() != b.canonical_hash()


def test_hash_and_diff_agree_for_every_money_field():
    """A hash change with no field change is a serialization bug, always."""
    base = detail_record()
    for field in ("estimated_value", "tender_security", "document_price"):
        unscaled = base.model_copy(update={field: Decimal("1500")})
        scaled = base.model_copy(update={field: Decimal("1500.00")})
        assert unscaled.canonical_hash() == scaled.canonical_hash(), field
        assert unscaled.changed_fields(scaled) == {}, field
