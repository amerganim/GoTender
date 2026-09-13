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
    MIN_VECTOR_SIMILARITY,
    build_tsquery,
    fuse,
    keywords_from_profile,
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
    assert fuse([(7, MIN_VECTOR_SIMILARITY - 0.01)], []) == []


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
