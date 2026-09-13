-- 007_embeddings.sql — pgvector storage for Layer 2 (§7).
--
-- Model: sentence-transformers/paraphrase-multilingual-mpnet-base-v2, 768
-- dimensions, run locally via ONNX. Chosen on measured evidence rather than
-- reputation; see section 13 of CLAUDE.md for the evaluation.
--
-- The dimension is baked into the column type, so changing model means a
-- migration AND re-embedding the whole corpus. It is recorded on every row so
-- a future model change can be done incrementally rather than all at once.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tender_embeddings (
    tender_id       BIGINT PRIMARY KEY REFERENCES tenders (id) ON DELETE CASCADE,
    embedding       vector(768) NOT NULL,
    model           TEXT        NOT NULL,
    source_text     TEXT,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE profile_embeddings (
    user_id         BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    embedding       vector(768) NOT NULL,
    model           TEXT        NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW over cosine distance. Built now while the table is small; building it
-- later against the full corpus takes far longer.
CREATE INDEX tender_embeddings_hnsw_idx
    ON tender_embeddings USING hnsw (embedding vector_cosine_ops);

-- Which tenders still need embedding. Cost trap #2 in spirit: embed once,
-- cache forever, never recompute what has not changed.
CREATE INDEX tenders_needs_embedding_idx ON tenders (id)
    WHERE status = 'live';
