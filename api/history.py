"""Chat history per anonymous visitor, kept for 30 days.

The visitor id is a random value from a cookie set by the web app; nothing else identifies the person.
"""
import json
import uuid

import psycopg

RETENTION_DAYS = 30
MEMORY_MESSAGES = 10  # the summary the bot remembers is built from this many recent messages

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY,
    visitor_id uuid NOT NULL,
    title text NOT NULL,
    summary text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_visitor ON conversations (visitor_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id bigserial PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
    role text NOT NULL,
    text text NOT NULL,
    sources jsonb,
    products jsonb,
    fallback boolean,
    trace_id text,
    vote smallint,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages (conversation_id, id);
"""

# Reads also skip expired rows, so nothing older than the retention period is ever shown
FRESH = f"updated_at > now() - interval '{RETENTION_DAYS} days'"


def init():
    with psycopg.connect() as conn:
        conn.execute(SCHEMA)


def cleanup() -> int:
    """Delete conversations (and their messages) older than the retention period."""
    with psycopg.connect() as conn:
        return conn.execute(f"DELETE FROM conversations WHERE NOT ({FRESH})").rowcount


def list_conversations(visitor_id: str) -> list[dict]:
    with psycopg.connect() as conn:
        rows = conn.execute(
            f"SELECT id, title, updated_at FROM conversations WHERE visitor_id = %s AND {FRESH} ORDER BY updated_at DESC",
            (visitor_id,),
        ).fetchall()
    return [{"id": str(i), "title": t, "updated_at": u.isoformat()} for i, t, u in rows]


def get_conversation(visitor_id: str, conversation_id: str) -> dict | None:
    """Returns None unless the conversation belongs to this visitor."""
    with psycopg.connect() as conn:
        row = conn.execute(
            f"SELECT summary FROM conversations WHERE id = %s AND visitor_id = %s AND {FRESH}",
            (conversation_id, visitor_id),
        ).fetchone()
        if not row:
            return None
        messages = conn.execute(
            "SELECT role, text, sources, products, fallback, trace_id, vote FROM messages WHERE conversation_id = %s ORDER BY id",
            (conversation_id,),
        ).fetchall()
    keys = ["role", "text", "sources", "products", "fallback", "trace_id", "vote"]
    return {"id": conversation_id, "summary": row[0], "messages": [dict(zip(keys, m)) for m in messages]}


def create_conversation(visitor_id: str, first_message: str) -> str:
    conversation_id = str(uuid.uuid4())
    title = first_message if len(first_message) <= 60 else first_message[:57] + "..."
    with psycopg.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, visitor_id, title) VALUES (%s, %s, %s)", (conversation_id, visitor_id, title)
        )
    return conversation_id


def add_message(conversation_id: str, role: str, text: str, result: dict | None = None):
    result = result or {}
    with psycopg.connect() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, text, sources, products, fallback, trace_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                conversation_id,
                role,
                text,
                json.dumps(result["sources"]) if "sources" in result else None,
                json.dumps(result["products"]) if "products" in result else None,
                result.get("fallback"),
                result.get("trace_id"),
            ),
        )
        conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,))


def recent_messages(conversation_id: str) -> list[tuple[str, str]]:
    with psycopg.connect() as conn:
        rows = conn.execute(
            "SELECT role, text FROM messages WHERE conversation_id = %s ORDER BY id DESC LIMIT %s",
            (conversation_id, MEMORY_MESSAGES),
        ).fetchall()
    return rows[::-1]


def set_summary(conversation_id: str, summary: str):
    with psycopg.connect() as conn:
        conn.execute("UPDATE conversations SET summary = %s WHERE id = %s", (summary, conversation_id))


def set_vote(visitor_id: str, trace_id: str, value: int):
    with psycopg.connect() as conn:
        conn.execute(
            "UPDATE messages m SET vote = %s FROM conversations c"
            " WHERE m.conversation_id = c.id AND c.visitor_id = %s AND m.trace_id = %s",
            (value, visitor_id, trace_id),
        )


def delete_visitor(visitor_id: str) -> list[str]:
    """Delete all of a visitor's chats; returns their trace ids so they can be removed from Langfuse too."""
    with psycopg.connect() as conn:
        trace_ids = conn.execute(
            "SELECT m.trace_id FROM messages m JOIN conversations c ON c.id = m.conversation_id"
            " WHERE c.visitor_id = %s AND m.trace_id IS NOT NULL",
            (visitor_id,),
        ).fetchall()
        conn.execute("DELETE FROM conversations WHERE visitor_id = %s", (visitor_id,))
    return [t for (t,) in trace_ids]
