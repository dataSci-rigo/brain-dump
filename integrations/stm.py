"""Semantic Task Manager integration. STM has no HTTP API, so writes are a
direct INSERT mirroring its db.create_task, and reads are a read-only
connection. STM's own _connect sets WAL, so a busy_timeout on our side is all
the cross-process safety needed."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


def _connect(readonly: bool = False) -> sqlite3.Connection | None:
    if config.STM_DB_PATH is None:
        return None
    uri = f"file:{config.STM_DB_PATH}" + ("?mode=ro" if readonly else "")
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def route_to_stm(title: str, description: str) -> int | None:
    """Insert a draft task; STM's own classify flow picks it up. Returns task id."""
    conn = _connect()
    if conn is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, description, status, source,"
                " created_at, updated_at) VALUES (?, ?, 'draft', 'braindump', ?, ?)",
                (title, description, now, now))
            return cur.lastrowid
    except sqlite3.Error:
        logger.warning("route_to_stm failed", exc_info=True)
        return None
    finally:
        conn.close()


def search_stm(terms: list[str], limit: int = 5) -> list[dict]:
    conn = _connect(readonly=True)
    if conn is None or not terms:
        return []
    try:
        clauses = " OR ".join(["title LIKE ? OR description LIKE ?"] * len(terms))
        params = []
        for t in terms:
            params += [f"%{t}%", f"%{t}%"]
        rows = conn.execute(
            f"SELECT id, title, status FROM tasks"
            f" WHERE status NOT IN ('done', 'dropped') AND ({clauses})"
            f" ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        logger.warning("search_stm failed", exc_info=True)
        return []
    finally:
        conn.close()
