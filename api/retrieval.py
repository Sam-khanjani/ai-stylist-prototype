"""Hybrid search over the knowledge chunks: vector similarity + full-text, merged with reciprocal rank fusion."""
import os

import numpy as np
import psycopg
from google import genai
from google.genai import types
from pgvector.psycopg import register_vector

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "ai-stylist-proto")
LOCATION = os.getenv("VERTEX_LOCATION", "europe-west4")
EMBED_MODEL = "gemini-embedding-001"  # must match ingest/embed.py
DIM = 768

_client = None

# Full-text query uses OR between words; plain to_tsquery would require every word to match
HYBRID_SQL = """
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(vec)s) AS rank
    FROM chunks ORDER BY embedding <=> %(vec)s LIMIT 20
), kw AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) AS rank
    FROM chunks, to_tsquery('english', replace(plainto_tsquery('english', %(text)s)::text, '&', '|')) q
    WHERE tsv @@ q ORDER BY ts_rank(tsv, q) DESC LIMIT 20
)
SELECT c.title, c.heading, c.source, c.content,
       coalesce(1.0 / (60 + vec.rank), 0) + coalesce(1.0 / (60 + kw.rank), 0) AS score
FROM chunks c
LEFT JOIN vec USING (id)
LEFT JOIN kw USING (id)
WHERE vec.id IS NOT NULL OR kw.id IS NOT NULL
ORDER BY score DESC
LIMIT %(k)s
"""


def embed_query(text: str) -> np.ndarray:
    global _client
    _client = _client or genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    config = types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=DIM)
    values = np.array(_client.models.embed_content(model=EMBED_MODEL, contents=text, config=config).embeddings[0].values)
    return values / np.linalg.norm(values)


PRODUCT_SQL = """
SELECT data FROM products
WHERE (%(section)s::text IS NULL OR section = %(section)s)
  AND (%(color)s::text IS NULL OR color ILIKE '%%' || %(color)s || '%%')
  AND (%(max_price)s::numeric IS NULL OR price <= %(max_price)s)
ORDER BY embedding <=> %(vec)s
LIMIT %(k)s
"""


def search_products(text: str, section=None, color=None, max_price=None, k: int = 8) -> list[dict]:
    """Hard filters from the router, ranked by meaning ("summer wedding" -> linen, light wool)."""
    vec = embed_query(text)
    with psycopg.connect() as conn:
        register_vector(conn)
        rows = conn.execute(
            PRODUCT_SQL, {"vec": vec, "section": section, "color": color, "max_price": max_price, "k": k}
        ).fetchall()
    return [row[0] for row in rows]


CONTACT_SQL = """
SELECT title, heading, source, content FROM chunks
WHERE split_part(id, ':', 1) IN ('footer-contact', 'customer-service')
   OR (source LIKE '%%/faq.html' AND heading ILIKE '%%appointment%%')
ORDER BY id
"""


def contact_sources() -> list[dict]:
    """Customer service channels and appointment info, fetched directly rather than searched."""
    with psycopg.connect() as conn:
        rows = conn.execute(CONTACT_SQL).fetchall()
    return [{"title": t, "heading": h, "url": s, "text": c} for t, h, s, c in rows]


def stores_in(country: str) -> list[dict]:
    with psycopg.connect() as conn:
        rows = conn.execute(
            "SELECT title, heading, source, content FROM chunks WHERE kind = 'store' AND country ILIKE %s ORDER BY title",
            (country,),
        ).fetchall()
    return [{"title": t, "heading": h, "url": s, "text": c} for t, h, s, c in rows]


def search(text: str, k: int = 6) -> list[dict]:
    vec = embed_query(text)
    # Connection settings come from PG* env vars (Cloud SQL socket on Cloud Run, proxy locally)
    with psycopg.connect() as conn:
        register_vector(conn)
        rows = conn.execute(HYBRID_SQL, {"vec": vec, "text": text, "k": k}).fetchall()
    return [{"title": t, "heading": h, "url": s, "text": c} for t, h, s, c, _ in rows]
