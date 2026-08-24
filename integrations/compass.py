"""hab7bot ("Compass") integration. Writes go through its REST API (capture
runs role/project/quadrant inference server-side) using the X-Api-Key auth
path patched into hab7bot. Reads are a read-only sqlite connection — no
search endpoint exists, and the whole read is try/except so Alembic schema
drift degrades to an empty section, never a crash."""
from __future__ import annotations

import logging
import sqlite3

import requests

import config

logger = logging.getLogger(__name__)


def route_to_compass(text: str) -> str | None:
    """POST /api/v1/capture. Returns the created task id (str) or None."""
    if not config.COMPASS_API_KEY:
        logger.warning("HAB7BOT_INTERNAL_API_KEY not set; cannot route to Compass")
        return None
    try:
        resp = requests.post(
            f"{config.COMPASS_API_URL}/api/v1/capture",
            json={"text": text, "origin": "braindump"},
            headers={"X-Api-Key": config.COMPASS_API_KEY},
            timeout=15,
        )
        if resp.ok:
            return str(resp.json().get("id"))
        logger.warning("Compass capture failed: %s %.200s", resp.status_code, resp.text)
    except requests.RequestException:
        logger.warning("Compass capture request error", exc_info=True)
    return None


def search_compass(terms: list[str], limit: int = 5) -> list[dict]:
    if config.COMPASS_DB_PATH is None or not terms:
        return []
    try:
        conn = sqlite3.connect(f"file:{config.COMPASS_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            clauses = " OR ".join(["title LIKE ?"] * len(terms))
            rows = conn.execute(
                f"SELECT id, title, status FROM tasks WHERE {clauses}"
                f" ORDER BY id DESC LIMIT ?",
                (*[f"%{t}%" for t in terms], limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        logger.warning("search_compass failed (schema drift?)", exc_info=True)
        return []
