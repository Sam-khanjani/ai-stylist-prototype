"""Read-only data for the admin dashboard. Only the web app can call the api, and it checks the admin login."""
import os
import uuid

import psycopg
from fastapi import APIRouter, HTTPException

import history

router = APIRouter(prefix="/admin")


def rows(sql: str, params=()) -> list[dict]:
    with psycopg.connect() as conn:
        cur = conn.execute(sql, params)
        names = [c.name for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


@router.get("/overview")
def overview(days: int = 30):
    return rows(
        """
        SELECT created_at::date AS day,
               count(*) AS questions,
               count(*) FILTER (WHERE route = 'policy') AS policy,
               count(*) FILTER (WHERE route = 'product') AS product,
               count(*) FILTER (WHERE route = 'other') AS other,
               count(*) FILTER (WHERE fallback) AS fallbacks,
               count(*) FILTER (WHERE vote = 1) AS up,
               count(*) FILTER (WHERE vote = 0) AS down,
               round(avg(latency_ms))::int AS avg_latency_ms
        FROM events
        WHERE created_at > now() - make_interval(days => %s)
        GROUP BY 1 ORDER BY 1
        """,
        (days,),
    )


@router.get("/conversations")
def conversations():
    return rows(
        f"""
        SELECT c.id::text, c.title, c.updated_at, count(m.id) AS messages,
               coalesce(bool_or(m.fallback), false) AS fallback,
               count(*) FILTER (WHERE m.vote = 1) AS up,
               count(*) FILTER (WHERE m.vote = 0) AS down
        FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.{history.FRESH}
        GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 200
        """
    )


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: uuid.UUID):
    found = rows(f"SELECT title, summary FROM conversations WHERE id = %s AND {history.FRESH}", (conversation_id,))
    if not found:
        raise HTTPException(404, "conversation not found")
    messages = rows(
        "SELECT role, text, sources, products, fallback, vote, created_at FROM messages WHERE conversation_id = %s ORDER BY id",
        (conversation_id,),
    )
    return found[0] | {"messages": messages}


@router.get("/gaps")
def gaps():
    """Answers that fell back to contact options or got a thumbs down, with the question that led to them."""
    return rows(
        """
        SELECT a.conversation_id::text, a.created_at, q.text AS question, a.text AS answer, a.fallback, a.vote
        FROM messages a
        JOIN LATERAL (
            SELECT text FROM messages q
            WHERE q.conversation_id = a.conversation_id AND q.role = 'user' AND q.id < a.id
            ORDER BY q.id DESC LIMIT 1
        ) q ON true
        WHERE a.role = 'assistant' AND (a.fallback OR a.vote = 0)
        ORDER BY a.created_at DESC LIMIT 200
        """
    )


@router.get("/evals")
def evals():
    return rows("SELECT run, created_at, summary, failures FROM eval_runs ORDER BY created_at DESC LIMIT 20")


@router.get("/status")
def status():
    counts = rows(
        """
        SELECT (SELECT count(*) FROM chunks) AS chunks,
               (SELECT count(*) FROM products) AS products,
               (SELECT count(*) FROM conversations) AS conversations,
               (SELECT count(*) FROM messages) AS messages,
               (SELECT count(*) FROM events) AS events,
               (SELECT min(updated_at) FROM conversations) AS oldest_conversation,
               now() AS db_time
        """
    )[0]
    return counts | {
        "retention_days": history.RETENTION_DAYS,
        "event_retention_days": history.EVENT_RETENTION_DAYS,
        "chat_model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "embedding_model": "gemini-embedding-001",
        "tracing": bool(os.getenv("LANGFUSE_SECRET_KEY")),
        # Cloud Run sets this to the deployed revision name
        "revision": os.getenv("K_REVISION", "local"),
    }
