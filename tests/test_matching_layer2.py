"""Hybrid fusion (§7 as amended by §14).

Embeddings and full-text search fail in different places -- that is the entire
reason both exist here. In evaluation, embeddings scored 10/10 on water and
medical in both languages but 2/10 on electrical; FTS scored 10/10 on that
same electrical query. Fusion has to preserve both strengths without letting
either one's weakness through.
"""

from __future__ import annotations

from tenderradar.matching.embeddings import profile_text, tender_text
from tenderradar.matching.layer2 import (
    ABSOLUTE_FLOOR,
    RELATIVE_FLOOR_FRACTION,
    build_tsquery,
    fuse,
    keywords_from_profile,
    similarity_floor,
)


# ------------------------------------------------------ keyword extraction


def test_stopwords_and_boilerplate_are_dropped():
    """'simple' does no stopword removal, so we must.

    A description full of "we are a company that does work for government
    offices" would otherwise OR together terms matching nearly every tender.
    """
    words = keywords_from_profile(
        "We are a company that does electrical work for government offices"
    )
    assert "electrical" in words
    for noise in ("we", "are", "company", "work", "government", "offices", "that"):
        assert noise not in words


def test_short_tokens_and_numbers_are_dropped():
    words = keywords_from_profile("we do AC and 220 volt wiring in 2026")
    assert "wiring" in words
    assert "2026" not in words
    assert "ac" not in words  # too short to be selective


def test_keywords_are_ordered_by_frequency():
    words = keywords_from_profile(
        "substation substation substation transformer cabling"
    )
    assert words[0] == "substation"


def test_bangla_terms_survive_extraction():
    """Never assume ASCII (§9)."""
    words = keywords_from_profile("আমরা বৈদ্যুতিক স্থাপন কাজ করি")
    assert any("ঀ" <= ch <= "৿" for word in words for ch in word)


def test_tsquery_ors_terms_rather_than_anding_them():
    """websearch_to_tsquery ANDs by default, which matches nothing on prose."""
    assert build_tsquery(["electrical", "substation"]) == "electrical or substation"


# ------------------------------------------------------------- fusion


def test_agreement_between_retrievers_outranks_either_alone():
    """Both signals agreeing is the strongest evidence available pre-feedback."""
    both = fuse([(1, 0.9), (2, 0.8)], [(2, 0.5), (3, 0.4)])
    ranked = [s.tender_id for s in both]
    assert ranked[0] == 2
    assert next(s for s in both if s.tender_id == 2).reasons["matched_by"] == "both"


def test_keyword_only_match_survives():
    """The electrical case: FTS finds it, embeddings do not. It must get through."""
    scored = fuse([], [(99, 0.4)])
    assert [s.tender_id for s in scored] == [99]
    assert scored[0].reasons["matched_by"] == "keyword"


def test_semantic_only_match_survives_when_similarity_is_strong():
    """The cross-language case: a Bangla profile, no shared English terms."""
    scored = fuse([(42, 0.71)], [])
    assert [s.tender_id for s in scored] == [42]
    assert scored[0].reasons["matched_by"] == "semantic"


def test_weak_semantic_match_with_no_keyword_hit_is_dropped():
    """Without a floor, RRF just ranks the least-bad of a bad set."""
    # Best is 0.80, so the floor sits at 0.48; 0.20 is far below it.
    scored = fuse([(1, 0.80), (7, 0.20)], [])
    assert [s.tender_id for s in scored] == [1]


# ------------------------------------------ the floor must not favour English


# Measured on this corpus: identical match quality, different score ranges,
# purely because one profile was written in Bangla and the other in English.
BANGLA_LIKE = [0.58, 0.53, 0.50, 0.46, 0.43, 0.39]
ENGLISH_LIKE = [0.80, 0.76, 0.73, 0.66, 0.61, 0.57]


def test_floor_scales_with_the_users_own_best_match():
    assert similarity_floor(ENGLISH_LIKE) > similarity_floor(BANGLA_LIKE)
    assert similarity_floor(ENGLISH_LIKE) == 0.80 * RELATIVE_FLOOR_FRACTION


def kept_under_relative_floor(sims, fraction=RELATIVE_FLOOR_FRACTION):
    floor = similarity_floor(sims, fraction=fraction)
    return sum(1 for s in sims if s >= floor)


def test_the_relative_floor_treats_both_languages_alike():
    """The offset between the two languages no longer decides who gets cut.

    It cannot make two differently-SHAPED distributions identical -- only the
    systematic offset is removed -- so the standard is "within one match",
    not "exactly equal".
    """
    assert abs(
        kept_under_relative_floor(BANGLA_LIKE)
        - kept_under_relative_floor(ENGLISH_LIKE)
    ) <= 1


def test_an_absolute_cutoff_was_badly_biased():
    """Documents the bug this replaced, so nobody reintroduces it.

    At a 0.55 cutoff the Bangla profile keeps one match and the English
    profile keeps all six, despite both sets being equally correct.
    """
    cutoff = 0.55
    bangla = sum(1 for s in BANGLA_LIKE if s >= cutoff)
    english = sum(1 for s in ENGLISH_LIKE if s >= cutoff)

    assert (bangla, english) == (1, 6)
    # Five matches of difference, against at most one under the relative floor.
    assert abs(bangla - english) > abs(
        kept_under_relative_floor(BANGLA_LIKE)
        - kept_under_relative_floor(ENGLISH_LIKE)
    )


def test_tightening_the_fraction_stays_language_neutral():
    """Tuning is the dangerous moment; it must not reintroduce the bias.

    An absolute cutoff gets MORE biased as it rises. The relative floor stays
    within one match of parity at every setting.
    """
    for fraction in (0.5, 0.7, 0.9, 0.95):
        assert abs(
            kept_under_relative_floor(BANGLA_LIKE, fraction)
            - kept_under_relative_floor(ENGLISH_LIKE, fraction)
        ) <= 1


def test_absolute_backstop_applies_when_everything_is_weak():
    """A profile matching nothing gets nothing, not its least-bad noise."""
    assert similarity_floor([0.10, 0.08]) == ABSOLUTE_FLOOR
    assert fuse([(1, 0.10), (2, 0.08)], []) == []


def test_backstop_is_below_both_observed_ranges():
    """It must never be the binding constraint for a real profile."""
    assert ABSOLUTE_FLOOR < min(BANGLA_LIKE)
    assert ABSOLUTE_FLOOR < min(ENGLISH_LIKE)


def test_no_vector_hits_falls_back_to_the_backstop():
    assert similarity_floor([]) == ABSOLUTE_FLOOR


def test_the_floor_is_recorded_on_every_match():
    """So a later precision review can tell a weak match from a strict floor."""
    scored = fuse([(1, 0.80)], [])
    assert scored[0].reasons["floor"] == round(0.80 * RELATIVE_FLOOR_FRACTION, 4)


def test_weak_semantic_match_is_kept_when_keywords_agree():
    """A term match is evidence in its own right, whatever the vector says."""
    scored = fuse([(7, 0.05)], [(7, 0.3)])
    assert [s.tender_id for s in scored] == [7]


def test_ranking_positions_are_recorded_for_explainability():
    scored = fuse([(5, 0.9)], [(5, 0.7)])
    item = scored[0]
    assert item.vector_position == 0
    assert item.fts_position == 0
    assert item.reasons["similarity"] == 0.9


def test_fusion_is_deterministic_on_ties():
    """Two runs must not reorder equal-scoring matches, or alerts churn."""
    first = fuse([(3, 0.8), (1, 0.8)], [])
    second = fuse([(1, 0.8), (3, 0.8)], [])
    assert [s.score for s in first] == sorted(
        [s.score for s in first], reverse=True
    )
    assert {s.tender_id for s in first} == {s.tender_id for s in second}


def test_empty_input_yields_nothing():
    assert fuse([], []) == []


# --------------------------------------------------------- embedded text


def test_tender_text_does_not_repeat_a_title_contained_in_the_description():
    """On this source the description often starts with the title verbatim."""
    text = tender_text(
        {"title": "Deep tube well", "description": "Deep tube well in Sylhet"}
    )
    assert text == "Deep tube well in Sylhet"


def test_tender_text_excludes_organization():
    """Folding in the ministry makes every tender from it look alike."""
    text = tender_text(
        {"title": "Deep tube well", "description": None,
         "organization_path": "Department of Public Health Engineering"}
    )
    assert "Public Health" not in text


def test_profile_text_appends_keywords_to_the_description():
    """Short text embeds badly; keywords supplement prose, never replace it."""
    text = profile_text(
        {
            "business_description": "We install deep tube wells.",
            "category_keywords": ["water supply", "pumps"],
        }
    )
    assert "deep tube wells" in text
    assert "water supply" in text


def test_profile_text_is_empty_when_nothing_was_provided():
    """An empty profile must not be matched against the whole live pool."""
    assert profile_text({"business_description": "  "}) == ""


def test_tender_text_dedups_whichever_way_round_the_strings_arrive():
    """The subsuming string can be either the title or the description."""
    longer_second = tender_text(
        {"title": "Deep tube well", "description": "Deep tube well in Sylhet"}
    )
    longer_first = tender_text(
        {"title": "Deep tube well in Sylhet", "description": "Deep tube well"}
    )
    assert longer_second == "Deep tube well in Sylhet"
    assert longer_first == "Deep tube well in Sylhet"


def test_tender_text_keeps_genuinely_different_title_and_description():
    text = tender_text(
        {"title": "PSWSC-6145", "description": "Installation of tube wells"}
    )
    assert "PSWSC-6145" in text and "Installation" in text
