"""Layer 1 hard filters (§7).

A false negative here is invisible: the user simply never hears about a tender
they could have won, and never complains, they just quietly stop paying. So
these tests are mostly about what Layer 1 must NOT drop.
"""

from __future__ import annotations

from decimal import Decimal

from tenderradar.matching.layer1 import Layer1Profile, build_clauses


def sql(profile: Layer1Profile) -> str:
    return " AND ".join(build_clauses(profile)[0])


def params(profile: Layer1Profile) -> dict:
    return build_clauses(profile)[1]


# ------------------------------------------------------------- the basics


def test_only_live_tenders_are_candidates():
    assert "t.status = 'live'" in sql(Layer1Profile())


def test_an_empty_profile_constrains_only_time():
    """No stated preferences means no filtering beyond the closing window."""
    clauses, _ = build_clauses(Layer1Profile())
    assert not any("district" in c for c in clauses)
    assert not any("procurement_nature" in c for c in clauses)
    assert not any("tender_security" in c for c in clauses)


def test_closing_window_excludes_the_unbiddable():
    """Something closing in two hours is noise, not an opportunity."""
    p = Layer1Profile(min_hours_to_closing=24, max_days_to_closing=90)
    assert "closing_at >= now()" in sql(p)
    assert params(p)["min_hours"] == 24
    assert params(p)["max_days"] == 90


# --------------------------------------------------- geography and intent


def test_district_filter_is_applied():
    p = Layer1Profile(district_names=["Sylhet", "Moulvibazar"])
    assert "t.district_name = ANY" in sql(p)
    assert params(p)["districts"] == ["Sylhet", "Moulvibazar"]


def test_unknown_district_is_kept_not_dropped():
    """District arrives with the detail page, which lags the list sweep.

    Dropping NULL districts would hide the newest tenders -- exactly the ones
    the 30-minute claim is about.
    """
    assert "t.district_name IS NULL" in sql(
        Layer1Profile(district_names=["Sylhet"])
    )


def test_nature_and_method_filters():
    p = Layer1Profile(
        procurement_natures=["works", "goods"], procurement_methods=["OTM"]
    )
    assert "t.procurement_nature = ANY" in sql(p)
    assert "t.procurement_method = ANY" in sql(p)
    assert params(p)["natures"] == ["works", "goods"]


# ------------------------------------------------------------ value range


def test_security_bounds_are_applied():
    p = Layer1Profile(
        min_security=Decimal("10000"), max_security=Decimal("500000")
    )
    assert params(p)["min_sec"] == Decimal("10000")
    assert params(p)["max_sec"] == Decimal("500000")


def test_unknown_security_is_kept_not_treated_as_zero():
    """NULL security is ignorance, not a value.

    e-GP publishes no estimated cost and security only arrives with the detail
    page. Excluding NULLs would drop every tender we have not enriched yet.
    """
    p = Layer1Profile(min_security=Decimal("10000"))
    assert "t.tender_security IS NULL OR" in sql(p)

    p = Layer1Profile(max_security=Decimal("500000"))
    assert "t.tender_security IS NULL OR" in sql(p)


def test_only_the_stated_bound_is_applied():
    p = Layer1Profile(min_security=Decimal("10000"))
    assert "min_sec" in params(p)
    assert "max_sec" not in params(p)


# ------------------------------------------------------- profile loading


def test_profile_loads_from_a_database_row():
    p = Layer1Profile.from_row(
        {
            "user_id": 7,
            "district_names": ["Sylhet"],
            "procurement_natures": ["works"],
            "procurement_methods": None,
            "min_security": Decimal("5000"),
            "max_security": None,
        }
    )
    assert p.user_id == 7
    assert p.district_names == ["Sylhet"]
    assert p.procurement_methods == []   # NULL becomes "no constraint"
    assert p.max_security is None


def test_null_arrays_become_no_constraint_not_an_empty_match():
    """A NULL array must not filter to nothing."""
    p = Layer1Profile.from_row(
        {"district_names": None, "procurement_natures": None}
    )
    clauses, _ = build_clauses(p)
    assert not any("district_name" in c for c in clauses)
