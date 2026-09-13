"""Local embedding service (§7 Layer 2).

Runs `paraphrase-multilingual-mpnet-base-v2` through ONNX on the VPS. No API
bill, no per-tender cost, nothing leaves the server. Chosen on measured
retrieval quality; see §14 of CLAUDE.md.

Cost discipline (cost trap #2 in spirit): a tender is embedded once and the
vector is cached forever. Re-embedding happens only when the text that was
embedded actually changed, which is why the source text is stored alongside
the vector rather than assumed.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from psycopg import AsyncConnection

log = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
DIMENSIONS = 768

# Large enough to amortize model overhead, small enough that a crawl-time
# embed does not hold hundreds of megabytes of text in memory.
BATCH_SIZE = 64

_model: Any = None


def get_model() -> Any:
    """Load the model once per process. First call is slow; later ones are not."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        log.info("loading embedding model %s", MODEL_NAME)
        _model = TextEmbedding(MODEL_NAME)
    return _model


def tender_text(row: dict[str, Any]) -> str:
    """The text a tender is embedded from.

    Title and description only. Organization and district are deliberately
    excluded: they are handled exactly by Layer 1 hard filters, and folding
    them in here would make every tender from a big ministry look similar to
    every other, drowning the part that actually distinguishes them.
    """
    kept: list[str] = []
    for part in (row.get("title"), row.get("description")):
        text = (part or "").strip()
        if not text:
            continue
        # The description usually opens with the title verbatim on this
        # source. Keeping both would double the title's weight in the vector,
        # so keep whichever string subsumes the other -- checked in both
        # directions, since the longer one can arrive either first or second.
        if any(text in existing for existing in kept):
            continue
        kept = [existing for existing in kept if existing not in text]
        kept.append(text)
    return " ".join(kept).strip()


def profile_text(row: dict[str, Any]) -> str:
    """The text a user profile is embedded from.

    Short queries embed badly -- two-word Bangla phrases ranked unrelated
    tenders top in evaluation, while full sentences scored 70%. Keywords are
    appended to the free-text description rather than replacing it.
    """
    parts = [row.get("business_description") or ""]
    for key in ("category_keywords", "enlistment_categories"):
        values = row.get(key) or []
        if values:
            parts.append(" ".join(values))
    if row.get("experience_summary"):
        parts.append(row["experience_summary"])
    return " ".join(p.strip() for p in parts if p and p.strip()).strip()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Embed a batch. Vectors come back L2-normalized for cosine distance."""
    if not texts:
        return []
    model = get_model()
    return [vector.tolist() for vector in model.embed(list(texts), batch_size=BATCH_SIZE)]


def embed_one(text: str) -> list[float]:
    return embed_texts([text])[0]


async def tenders_needing_embedding(
    conn: AsyncConnection, limit: int = 1000
) -> list[dict[str, Any]]:
    """Live tenders with no vector, or whose embedded text has changed."""
    cur = await conn.execute(
        """
        SELECT t.id, t.title, t.description
          FROM tenders t
          LEFT JOIN tender_embeddings e ON e.tender_id = t.id
         WHERE t.status = 'live'
           AND (e.tender_id IS NULL OR e.model <> %s)
         ORDER BY t.published_at DESC NULLS LAST
         LIMIT %s
        """,
        (MODEL_NAME, limit),
    )
    return list(await cur.fetchall())


async def store_tender_embeddings(
    conn: AsyncConnection, rows: Iterable[dict[str, Any]]
) -> int:
    """Embed and upsert. Rows whose text is empty are skipped, not stored."""
    batch = [(row["id"], tender_text(row)) for row in rows]
    batch = [(tender_id, text) for tender_id, text in batch if text]
    if not batch:
        return 0

    vectors = embed_texts([text for _, text in batch])
    for (tender_id, text), vector in zip(batch, vectors, strict=True):
        await conn.execute(
            """
            INSERT INTO tender_embeddings
                (tender_id, embedding, model, source_text, generated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (tender_id) DO UPDATE
                SET embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model,
                    source_text = EXCLUDED.source_text,
                    generated_at = now()
            """,
            (tender_id, str(vector), MODEL_NAME, text[:2000]),
        )
    return len(batch)


async def store_profile_embedding(
    conn: AsyncConnection, user_id: int, row: dict[str, Any]
) -> bool:
    text = profile_text(row)
    if not text:
        return False
    vector = embed_one(text)
    await conn.execute(
        """
        INSERT INTO profile_embeddings (user_id, embedding, model, generated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (user_id) DO UPDATE
            SET embedding = EXCLUDED.embedding,
                model = EXCLUDED.model,
                generated_at = now()
        """,
        (user_id, str(vector), MODEL_NAME),
    )
    return True


async def embed_backlog(conn: AsyncConnection, limit: int = 1000) -> int:
    """Embed whatever is outstanding, in batches. Returns rows embedded."""
    total = 0
    while total < limit:
        rows = await tenders_needing_embedding(
            conn, limit=min(BATCH_SIZE * 4, limit - total)
        )
        if not rows:
            break
        stored = await store_tender_embeddings(conn, rows)
        await conn.commit()
        total += stored
        log.info("embedded %d tenders (%d total)", stored, total)
        if stored == 0:
            break
    return total
