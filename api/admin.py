"""Read-only data for the admin dashboard. Only the web app can call the api, and it checks the admin login."""
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

import db
import history

router = APIRouter(prefix="/admin")
CACHE_SECONDS = 60  # Langfuse answers are cached this long; its API takes ~1 s or more per call
LANGFUSE_LIMITS = {"timeout_in_seconds": 8, "max_retries": 0}


def describe(e: Exception) -> str:
    """Short readable error: Langfuse's ApiError puts the headers first, which hides the actual message."""
    body = getattr(e, "body", None)
    message = (body.get("message") or body.get("error") or str(body)) if isinstance(body, dict) else (body or str(e))
    status = getattr(e, "status_code", None)
    return f"{status} · {message}"[:200] if status else str(message)[:200]


def rows(sql: str, params=()) -> list[dict]:
    with db.connection() as conn:
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
    return rows(
        "SELECT run, created_at, summary, failures, gate_passed, baseline, regressions, fixed, saved_as_baseline"
        " FROM eval_runs ORDER BY created_at DESC LIMIT 30"
    )


AGENT_STEPS = ["intent", "policy_search", "product_search", "answer", "judge", "smalltalk", "recall", "fallback"]
NOT_EVAL = {"column": "environment", "operator": "none of", "value": ["eval"], "type": "stringOptions"}  # eval runs tag themselves


def any_of(column: str, values: list[str]) -> dict:
    return {"column": column, "operator": "any of", "value": values, "type": "stringOptions"}


_cache: dict[int, tuple[float, dict]] = {}


@router.get("/langfuse")
def langfuse(days: int = 7):
    """Cost, tokens, latency and errors from Langfuse's Metrics API.

    The queries run in parallel and the result is cached for a minute: each call takes ~1 s, and the
    dashboard doesn't need second-by-second numbers.
    """
    if not os.getenv("LANGFUSE_SECRET_KEY"):
        return {"enabled": False}
    cached = _cache.get(days)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    from langfuse import get_client

    client = get_client()
    now = datetime.now(timezone.utc)
    window = {"fromTimestamp": (now - timedelta(days=days)).isoformat(), "toTimestamp": now.isoformat()}

    def query(view, metrics, dimensions=(), filters=(), granularity=None):
        q = {
            "view": view,
            "metrics": [{"measure": m, "aggregation": a} for m, a in metrics],
            "dimensions": [{"field": d} for d in dimensions],
            "filters": [*filters, NOT_EVAL],
            **window,
        }
        if granularity:
            q["timeDimension"] = {"granularity": granularity}
        # Without a timeout a slow Langfuse hangs the request (and the dashboard waiting on it)
        return client.api.metrics.metrics(query=json.dumps(q), request_options=LANGFUSE_LIMITS).data

    llm_calls = [any_of("type", ["GENERATION"])]
    usage = [("totalCost", "sum"), ("inputTokens", "sum"), ("outputTokens", "sum"), ("count", "count")]
    sections = {
        "usage": lambda: query("observations", usage, filters=llm_calls),
        "daily": lambda: query("observations", usage, filters=llm_calls, granularity="day"),
        "models": lambda: query("observations", [*usage, ("latency", "p95")], ["providedModelName"], llm_calls),
        # whole answers: the root span of each run is named after the agent
        "end_to_end": lambda: query(
            "observations", [("latency", "p50"), ("latency", "p95"), ("latency", "p99"), ("count", "count")],
            filters=[any_of("name", ["stylist-agent"])],
        ),
        "steps": lambda: query(
            "observations", [("latency", "p50"), ("latency", "p95"), ("count", "count")], ["name"], [any_of("name", AGENT_STEPS)]
        ),
        "errors": lambda: query("observations", [("count", "count")], ["name"], [any_of("level", ["ERROR"])]),
    }

    def safe(run):
        try:
            return run()
        except Exception as e:  # one failing query shouldn't hide the rest
            return {"error": describe(e)}

    with ThreadPoolExecutor(len(sections)) as pool:
        results = dict(zip(sections, pool.map(safe, sections.values())))
    result = {"enabled": True, "days": days} | results
    _cache[days] = (time.monotonic(), result)
    return result


_requests_cache: dict[int, tuple[float, dict]] = {}


@router.get("/requests")
def requests(limit: int = 30):
    """Latest answered questions: time, feature (route), latency and vote from our events table,
    tokens and cost per request from Langfuse's observations endpoint (one call, grouped by trace)."""
    cached = _requests_cache.get(limit)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    events = rows(
        "SELECT created_at, route, fallback, latency_ms, vote, trace_id FROM events ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    result = {"requests": events, "langfuse": "off"}
    if events and os.getenv("LANGFUSE_SECRET_KEY"):
        from langfuse import get_client

        try:
            calls = get_client().api.observations.get_many(
                type="GENERATION",
                fields="core,usage,model",
                from_start_time=min(e["created_at"] for e in events) - timedelta(minutes=1),
                limit=1000,
                request_options=LANGFUSE_LIMITS,
            ).data
            per_trace = defaultdict(lambda: {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "llm_calls": 0, "models": set()})
            for c in calls:
                t = per_trace[c.trace_id]
                usage = c.usage_details or {}
                t["tokens_in"] += usage.get("input", 0)
                t["tokens_out"] += usage.get("output", 0)
                t["cost"] += c.total_cost or 0
                t["llm_calls"] += 1
                if c.model:
                    t["models"].add(c.model)
            for e in events:
                t = per_trace.get(e["trace_id"])
                if t:
                    e.update(t, models=sorted(t["models"]))
            result["langfuse"] = "ok"
        except Exception as e:  # Langfuse slow or down: still show our own numbers
            result["langfuse"] = f"error: {describe(e)}"
    for e in events:
        e.pop("trace_id")
    _requests_cache[limit] = (time.monotonic(), result)
    return result


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
