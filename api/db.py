"""Shared Postgres connection pool.

Opening a connection per query costs a full handshake (slow through the Cloud SQL proxy), and the small
Cloud SQL tier only allows ~25 connections. The pool keeps a few open and reuses them.
Connection settings come from the PG* env vars.
"""
import atexit

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

_pool = None


def _configure(conn: psycopg.Connection):
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        pass  # vector extension not created yet (fresh database before ingestion)
    conn.commit()


def connection():
    """Use as `with db.connection() as conn:`; commits on success like psycopg.connect()."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(min_size=1, max_size=5, configure=_configure, check=ConnectionPool.check_connection)
        atexit.register(_pool.close)  # close cleanly when a script (eval, ingest) or the server exits
    return _pool.connection()
