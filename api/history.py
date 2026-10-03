"""Chat history per anonymous visitor, kept for CHAT_RETENTION_DAYS (default 30).

The visitor id is a random value from a cookie set by the web app; nothing else identifies the person.
"""
import json
import os
import uuid

import db

RETENTION_DAYS = int(os.getenv("CHAT_RETENTION_DAYS", "30"))
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

-- One row per answered question, without any text or visitor id, so usage trends outlive the chats
CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    route text,
    fallback boolean,
    latency_ms integer,
    vote smallint,
    trace_id text
);
CREATE INDEX IF NOT EXISTS events_created ON events (created_at);
CREATE INDEX IF NOT EXISTS events_trace ON events (trace_id);

-- Summaries of eval/run.py runs, shown in the admin dashboard
CREATE TABLE IF NOT EXISTS eval_runs (
    run text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    summary jsonb NOT NULL,
    failures jsonb NOT NULL
);

-- Added later; IF NOT EXISTS keeps existing databases working
ALTER TABLE messages ADD COLUMN IF NOT EXISTS route text;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS latency_ms integer;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS gate_passed boolean;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS baseline text;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS regressions jsonb;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS fixed jsonb;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS saved_as_baseline boolean;
"""

EVENT_RETENTION_DAYS = 365

# Reads also skip expired rows, so nothing older than the retention period is ever shown
FRESH = f"updated_at > now() - interval '{RETENTION_DAYS} days'"


def init():
    with db.connection() as conn:
        conn.execute(SCHEMA)


def cleanup() -> int:
    """Delete conversations (and their messages) older than the retention period."""
    with db.connection() as conn:
        deleted = conn.execute(f"DELETE FROM conversations WHERE NOT ({FRESH})").rowcount
        # Events keep only counts; drop their link to the (text-holding) Langfuse trace with the chat
        conn.execute(f"UPDATE events SET trace_id = NULL WHERE trace_id IS NOT NULL AND created_at < now() - interval '{RETENTION_DAYS} days'")
        conn.execute(f"DELETE FROM events WHERE created_at < now() - interval '{EVENT_RETENTION_DAYS} days'")
    return deleted


def record_event(route: str, fallback: bool, latency_ms: int, trace_id: str | None):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO events (route, fallback, latency_ms, trace_id) VALUES (%s, %s, %s, %s)",
            (route, fallback, latency_ms, trace_id),
        )


def list_conversations(visitor_id: str) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT id, title, updated_at FROM conversations WHERE visitor_id = %s AND {FRESH} ORDER BY updated_at DESC",
            (visitor_id,),
        ).fetchall()
    return [{"id": str(i), "title": t, "updated_at": u.isoformat()} for i, t, u in rows]


def get_conversation(visitor_id: str, conversation_id: str) -> dict | None:
    """Returns None unless the conversation belongs to this visitor."""
    with db.connection() as conn:
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
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO conversations (id, visitor_id, title) VALUES (%s, %s, %s)", (conversation_id, visitor_id, title)
        )
    return conversation_id


def add_message(conversation_id: str, role: str, text: str, result: dict | None = None, latency_ms: int | None = None):
    result = result or {}
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, text, sources, products, fallback, trace_id, route, latency_ms)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                conversation_id,
                role,
                text,
                json.dumps(result["sources"]) if "sources" in result else None,
                json.dumps(result["products"]) if "products" in result else None,
                result.get("fallback"),
                result.get("trace_id"),
                result.get("route"),
                latency_ms,
            ),
        )
        conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,))


def recent_messages(conversation_id: str) -> list[tuple[str, str]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT role, text FROM messages WHERE conversation_id = %s ORDER BY id DESC LIMIT %s",
            (conversation_id, MEMORY_MESSAGES),
        ).fetchall()
    return rows[::-1]


def set_summary(conversation_id: str, summary: str):
    with db.connection() as conn:
        conn.execute("UPDATE conversations SET summary = %s WHERE id = %s", (summary, conversation_id))


def set_vote(visitor_id: str, trace_id: str, value: int):
    with db.connection() as conn:
        conn.execute(
            "UPDATE messages m SET vote = %s FROM conversations c"
            " WHERE m.conversation_id = c.id AND c.visitor_id = %s AND m.trace_id = %s",
            (value, visitor_id, trace_id),
        )
        conn.execute("UPDATE events SET vote = %s WHERE trace_id = %s", (value, trace_id))


def delete_visitor(visitor_id: str) -> list[str]:
    """Delete all of a visitor's chats; returns their trace ids so they can be removed from Langfuse too."""
    with db.connection() as conn:
        trace_ids = conn.execute(
            "SELECT m.trace_id FROM messages m JOIN conversations c ON c.id = m.conversation_id"
            " WHERE c.visitor_id = %s AND m.trace_id IS NOT NULL",
            (visitor_id,),
        ).fetchall()
        conn.execute("DELETE FROM conversations WHERE visitor_id = %s", (visitor_id,))
    return [t for (t,) in trace_ids]


def last_sources(conversation_id: str) -> list[str]:
    """Urls the assistant's previous answer cited (pages or products), for follow-ups like "explain more"."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT sources FROM messages WHERE conversation_id = %s AND role = 'assistant' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return [s["url"] for s in (row[0] or [])] if row else []
