"""Embed the knowledge pages, stores and products with Vertex AI and load them into pgvector.

Connection settings come from the standard PG* env vars (PGHOST, PGUSER, PGPASSWORD, PGDATABASE).
Every run rebuilds the tables, so it is safe to rerun after a new crawl.
"""
import argparse
import json
import os
import re

import numpy as np
import psycopg
from google import genai
from google.genai import types
from pgvector.psycopg import register_vector

from crawl import OUT

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "ai-stylist-proto")
LOCATION = os.getenv("VERTEX_LOCATION", "europe-west4")
# gemini-embedding-2 is not offered in europe-west4 yet; 001 keeps queries in the EU
MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
DIM = 768
MAX_CHARS = 1600  # ~400 tokens per chunk
KNOWLEDGE = OUT / "knowledge"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS chunks (
    id text PRIMARY KEY,
    kind text NOT NULL,
    source text NOT NULL,
    title text,
    heading text,
    country text,
    content text NOT NULL,
    embedding vector({DIM}) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(heading, '') || ' ' || content)
    ) STORED
);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);

-- Filter columns for exact constraints, data holds the full product for cards
CREATE TABLE IF NOT EXISTS products (
    id text PRIMARY KEY,
    section text NOT NULL,
    color text,
    price numeric,
    data jsonb NOT NULL,
    embedding vector({DIM}) NOT NULL
);
"""


def sections(body):
    """Yield (heading path, text) for every markdown section."""
    path, lines = [], []
    for line in body.splitlines() + ["# end"]:
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        if not heading:
            if line.strip() != "All topics":
                lines.append(line)
            continue
        text = "\n".join(lines).strip()
        if text:
            yield [title for _, title in path], text
        level = len(heading[1])
        path = [(lvl, t) for lvl, t in path if lvl < level] + [(level, heading[2].strip("* "))]
        lines = []


def split(text):
    """Split long sections on paragraphs, repeating the last paragraph as overlap."""
    if len(text) <= MAX_CHARS:
        return [text]
    parts, current = [], []
    for para in text.split("\n\n"):
        if current and len("\n\n".join(current + [para])) > MAX_CHARS:
            parts.append("\n\n".join(current))
            current = current[-1:]
        current.append(para)
    parts.append("\n\n".join(current))
    return parts


def page_chunks():
    chunks, seen = [], set()
    # Help pages first, so shared sections are credited to them rather than journal pages
    for path in sorted((KNOWLEDGE / "pages").glob("*.md"), key=lambda p: (p.stem.startswith("journal_"), p.stem)):
        front, body = re.match(r"(?s)^---\n(.*?)\n---\n(.*)", path.read_text()).groups()
        meta = dict(line.split(": ", 1) for line in front.splitlines())
        n = 0
        for heading, text in sections(body):
            for part in split(text):
                # skip labels/buttons ("Fit Guide", "Learn more") and sections repeated on other pages
                if len(part.split()) < 4 or part in seen:
                    continue
                seen.add(part)
                chunks.append({
                    "id": f"{path.stem}:{n}",
                    "kind": "page",
                    "source": meta["source"],
                    "title": meta["title"],
                    "heading": " > ".join(heading),
                    "country": None,
                    "content": part,
                })
                n += 1
    return chunks


def store_chunks():
    chunks = []
    for s in json.loads((KNOWLEDGE / "stores.json").read_text()):
        country = s["country"].replace("-", " ").title()
        hours = "; ".join(f"{d.title()} {s['opening_hours'][d]}" for d in DAYS if d in s["opening_hours"])
        content = "\n".join(filter(None, [
            f"Suitsupply store {s['name']} ({country})",
            f"Address: {', '.join(s['address'])}",
            s["phone"] and f"Phone: {s['phone']}",
            s["email"] and f"Email: {s['email']}",
            f"Opening hours: {hours}",
        ]))
        chunks.append({
            "id": f"store:{s['url'].rsplit('/', 1)[1]}",
            "kind": "store",
            "source": s["url"],
            "title": f"Store {s['name']}",
            "heading": country,
            "country": country,
            "content": content,
        })
    return chunks


def product_docs():
    docs = []
    for line in (OUT / "products.jsonl").read_text().splitlines():
        p = json.loads(line)
        docs.append({
            "id": p["id"],
            "section": p["url"].split("/")[5],  # .../en-nl/men/suits/... -> suits
            "color": p["color"],
            "price": p["price"],
            "data": json.dumps(p),
            # embed() reads title/heading/content
            "title": p["name"],
            "heading": p["category"],
            "content": f"{p['name']}. Color: {p['color']}. Material: {p['material']}. {p['description']}",
        })
    return docs


def embed(client, chunk):
    text = f"{chunk['heading']}\n\n{chunk['content']}" if chunk["heading"] else chunk["content"]
    if "embedding-2" in MODEL:
        # embedding-2 takes the task as a prompt format instead of a task_type
        contents = f"title: {chunk['title'] or 'none'} | text: {text}"
        config = types.EmbedContentConfig(output_dimensionality=DIM)
    else:
        contents = text
        config = types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", title=chunk["title"], output_dimensionality=DIM)
    values = np.array(client.models.embed_content(model=MODEL, contents=contents, config=config).embeddings[0].values)
    return values / np.linalg.norm(values)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="only chunk and print stats")
    args = ap.parse_args()

    chunks = page_chunks() + store_chunks()
    products = product_docs()
    sizes = [len(c["content"]) for c in chunks]
    print(f"{len(chunks)} chunks, {min(sizes)}-{max(sizes)} chars, avg {sum(sizes) // len(sizes)}; {len(products)} products")
    if args.dry_run:
        return

    # Connect first so a broken connection fails before paying for embeddings
    with psycopg.connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        conn.execute(SCHEMA)

        client = genai.Client(
            vertexai=True,
            project=PROJECT,
            location=LOCATION,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=6)),
        )
        docs = chunks + products
        for i, doc in enumerate(docs, 1):
            doc["embedding"] = embed(client, doc)
            if i % 50 == 0 or i == len(docs):
                print(f"embedded {i}/{len(docs)}")

        conn.execute("TRUNCATE chunks, products")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (id, kind, source, title, heading, country, content, embedding)"
                " VALUES (%(id)s, %(kind)s, %(source)s, %(title)s, %(heading)s, %(country)s, %(content)s, %(embedding)s)",
                chunks,
            )
            cur.executemany(
                "INSERT INTO products (id, section, color, price, data, embedding)"
                " VALUES (%(id)s, %(section)s, %(color)s, %(price)s, %(data)s, %(embedding)s)",
                products,
            )
    print(f"loaded {len(chunks)} chunks and {len(products)} products")


if __name__ == "__main__":
    main()
