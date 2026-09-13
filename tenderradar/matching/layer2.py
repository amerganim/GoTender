"""Layer 2 — hybrid semantic ranking (§7, amended by §14).

Two retrievers over the Layer 1 candidate set, fused:

* **Vector** (pgvector cosine) carries meaning and crosses languages. A Bangla
  profile retrieves English tenders at 70% precision@10, against 73% for
  English profiles.
* **Full-text** (the Phase 1 index) carries exact domain terms.

They fail in different places, which is the whole reason for combining them.
In evaluation, embeddings scored 10/10 on water and medical in both languages
but 2/10 on electrical, where a generically worded profile matched boilerplate
instead of the trade. FTS scored 10/10 on that same query.

Fusion is Reciprocal Rank Fusion rather than a weighted sum of scores. Cosine
similarity and ts_rank live on incomparable scales, and any fixed weighting
between them would need recalibrating whenever either changes. RRF only reads
positions, so it needs no calibration at all.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from psycopg import AsyncConnection

from tenderradar.matching.embeddings import MODEL_NAME

log = logging.getLogger(__name__)

# Standard RRF damping. Large enough that the top few ranks do not dominate
# outright, small enough that deep results stop mattering.
RRF_K = 60

# A candidate must clear one of these to be worth a user's attention at all.
# Without a floor, RRF happily ranks the least-bad of a bad set.
MIN_VECTOR_SIMILARITY = 0.25

_WORD_RE = re.compile(r"[\wঀ-৿]+", re.UNICODE)

# 'simple' does no stopword removal, so common words would otherwise dominate
# the OR query and match nearly every tender.
_STOPWORDS = frozenset("""
a an and are as at be by for from has have in is it its of on or our
that the their they this to we will with you your
work works service services supply company limited ltd firm business
do does doing govt government office offices
""".split())


@dataclass(slots=True)
class ScoredTender:
    tender_id: int
    score: float
    vector_similarity: float | None = None
    fts_rank: float | None = None
    vector_position: int | None = None
    fts_position: int | None = None
    reasons: dict[str, Any] = field(default_factory=dict)


def keywords_from_profile(text: str, limit: int = 12) -> list[str]:
    """Significant terms for the FTS arm, commonest-first, stopwords dropped."""
    counts: dict[str, int] = {}
    for match in _WORD_RE.finditer(text.lower()):
        word = match.group(0)
        if len(word) < 3 or word in _STOPWORDS or word.isdigit():
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts, key=lambda w: (-counts[w], w))
    return ranked[:limit]


def build_tsquery(keywords: list[str]) -> str:
    """OR the terms together.

    websearch_to_tsquery ANDs by default, which on a multi-sentence business
    description matches nothing at all.
    """
    return " or ".join(keywords)


async def vector_candidates(
    conn: AsyncConnection,
    user_id: int,
    candidate_ids: list[int],
    *,
    limit: int = 100,
) -> list[tuple[int, float]]:
    """Layer 1 survivors ranked by cosine similarity to the user's profile."""
    if not candidate_ids:
        return []
    cur = await conn.execute(
        """
        SELECT e.tender_id,
               1 - (e.embedding <=> p.embedding) AS similarity
          FROM tender_embeddings e
          CROSS JOIN profile_embeddings p
         WHERE p.user_id = %(user_id)s
           AND e.tender_id = ANY(%(ids)s)
           AND e.model = %(model)s
         ORDER BY e.embedding <=> p.embedding
         LIMIT %(limit)s
        """,
        {
            "user_id": user_id,
            "ids": candidate_ids,
            "model": MODEL_NAME,
            "limit": limit,
        },
    )
    return [(row["tender_id"], float(row["similarity"])) for row in await cur.fetchall()]


async def fts_candidates(
    conn: AsyncConnection,
    query: str,
    candidate_ids: list[int],
    *,
    limit: int = 100,
) -> list[tuple[int, float]]:
    """Layer 1 survivors ranked by full-text relevance to the profile terms."""
    if not candidate_ids or not query:
        return []
    cur = await conn.execute(
        """
        SELECT t.id AS tender_id,
               ts_rank_cd(t.search_vector,
                          websearch_to_tsquery('simple', %(q)s)) AS rank
          FROM tenders t
         WHERE t.id = ANY(%(ids)s)
           AND t.search_vector @@ websearch_to_tsquery('simple', %(q)s)
         ORDER BY rank DESC
         LIMIT %(limit)s
        """,
        {"q": query, "ids": candidate_ids, "limit": limit},
    )
    return [(row["tender_id"], float(row["rank"])) for row in await cur.fetchall()]


def fuse(
    vector_hits: list[tuple[int, float]],
    fts_hits: list[tuple[int, float]],
    *,
    min_similarity: float = MIN_VECTOR_SIMILARITY,
) -> list[ScoredTender]:
    """Reciprocal Rank Fusion over the two result lists.

    A tender found by both retrievers outranks one found by either alone, which
    is the behaviour we want: agreement between an exact-term match and a
    semantic match is the strongest signal available without user feedback.
    """
    vector_rank = {tid: i for i, (tid, _) in enumerate(vector_hits)}
    fts_rank = {tid: i for i, (tid, _) in enumerate(fts_hits)}
    vector_score = dict(vector_hits)
    fts_score = dict(fts_hits)

    scored: list[ScoredTender] = []
    for tender_id in set(vector_rank) | set(fts_rank):
        similarity = vector_score.get(tender_id)
        in_fts = tender_id in fts_rank

        # Floor: a weak semantic match with no term match is not worth sending.
        # A term match is allowed through on its own -- that is exactly the
        # electrical case embeddings miss.
        if not in_fts and (similarity is None or similarity < min_similarity):
            continue

        score = 0.0
        if tender_id in vector_rank:
            score += 1.0 / (RRF_K + vector_rank[tender_id] + 1)
        if in_fts:
            score += 1.0 / (RRF_K + fts_rank[tender_id] + 1)

        matched_by = (
            "both" if tender_id in vector_rank and in_fts
            else "semantic" if tender_id in vector_rank
            else "keyword"
        )
        scored.append(
            ScoredTender(
                tender_id=tender_id,
                score=score,
                vector_similarity=similarity,
                fts_rank=fts_score.get(tender_id),
                vector_position=vector_rank.get(tender_id),
                fts_position=fts_rank.get(tender_id),
                # Carried into matches.reasons: a user who can see why a tender
                # was surfaced gives far more useful thumbs-down feedback.
                reasons={
                    "matched_by": matched_by,
                    "similarity": round(similarity, 4) if similarity is not None else None,
                },
            )
        )

    scored.sort(key=lambda s: (-s.score, s.tender_id))
    return scored


async def rank(
    conn: AsyncConnection,
    user_id: int,
    profile_text: str,
    candidate_ids: list[int],
    *,
    limit: int = 50,
) -> list[ScoredTender]:
    """Full Layer 2: retrieve both ways over Layer 1 survivors, then fuse."""
    keywords = keywords_from_profile(profile_text)
    query = build_tsquery(keywords)

    vector_hits = await vector_candidates(conn, user_id, candidate_ids)
    fts_hits = await fts_candidates(conn, query, candidate_ids)

    log.info(
        "layer2 for user %s: %d candidates -> %d vector, %d fts",
        user_id, len(candidate_ids), len(vector_hits), len(fts_hits),
    )
    return fuse(vector_hits, fts_hits)[:limit]
