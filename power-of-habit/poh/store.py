"""power-of-habit storage: fear/why concept taxonomy, quips, scan marks, and
scheduler state — new tables in the same notes.db, same per-call-connection
pattern as brain-dump's db.py (WAL + busy_timeout → thread-safe)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import config

POH_SCHEMA = """
CREATE TABLE IF NOT EXISTS poh_concepts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('fear','why')),
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'web' CHECK (source IN ('web','personal')),
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    UNIQUE (kind, text)
);

CREATE TABLE IF NOT EXISTS poh_quips (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id          INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    concept_id       INTEGER REFERENCES poh_concepts(id) ON DELETE SET NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('fear','why')),
    excerpt          TEXT,
    quip             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    sent_message_id  TEXT,
    mantra_count     INTEGER NOT NULL DEFAULT 0,
    last_mantra_at   TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS poh_quip_note_concept
    ON poh_quips(note_id, concept_id) WHERE concept_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS poh_note_scans (
    note_id             INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    scanned_updated_at  TEXT NOT NULL,
    scanned_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poh_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(POH_SCHEMA)


# ── state ─────────────────────────────────────────────────────────────────────

def get_state(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM poh_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO poh_state (key, value) VALUES (?, ?)",
                     (key, value))


# ── concepts ──────────────────────────────────────────────────────────────────

def active_concepts(kind: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM poh_concepts WHERE active = 1"
    params: tuple = ()
    if kind:
        sql += " AND kind = ?"
        params = (kind,)
    with _connect() as conn:
        return conn.execute(sql + " ORDER BY id", params).fetchall()


def add_concepts(kind: str, texts: list[str], source: str) -> int:
    """INSERT OR IGNORE each; returns how many were actually new."""
    now = _now()
    inserted = 0
    with _connect() as conn:
        for text in texts:
            text = " ".join(str(text).split()).strip().rstrip(".")
            if not text:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO poh_concepts (kind, text, source, created_at)"
                " VALUES (?, ?, ?, ?)", (kind, text, source, now))
            inserted += cur.rowcount
    return inserted


def get_concept(concept_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM poh_concepts WHERE id = ?",
                            (concept_id,)).fetchone()


def concept_id_for(kind: str, text: str, source: str) -> int:
    """Insert-if-missing and return the concept id."""
    text = " ".join(str(text).split()).strip().rstrip(".")
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO poh_concepts (kind, text, source, created_at)"
            " VALUES (?, ?, ?, ?)", (kind, text, source, now))
        row = conn.execute(
            "SELECT id FROM poh_concepts WHERE kind = ? AND text = ?",
            (kind, text)).fetchone()
    return row["id"]


# ── quips ─────────────────────────────────────────────────────────────────────

def insert_quip(*, note_id: int | None, concept_id: int | None, kind: str,
                excerpt: str | None, quip: str) -> int | None:
    """Returns the new quip id, or None when the (note, concept) unique index
    rejects it (already quipped this concept on this note)."""
    now = _now()
    with _connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO poh_quips (note_id, concept_id, kind, excerpt, quip,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (note_id, concept_id, kind, excerpt, quip, now, now))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def get_quip(quip_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM poh_quips WHERE id = ?",
                            (quip_id,)).fetchone()


def set_quip_status(quip_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE poh_quips SET status = ?, updated_at = ? WHERE id = ?",
                     (status, _now(), quip_id))


def set_quip_sent(quip_id: int, message_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE poh_quips SET sent_message_id = ?, updated_at = ?"
                     " WHERE id = ?", (message_id, _now(), quip_id))


def kept_quips(limit: int = 50) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM poh_quips WHERE status = 'kept'"
            " ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()


def pick_mantra_quip() -> sqlite3.Row | None:
    """A kept quip: never-sent ones first, then least-recently-sent, random
    tiebreak within a group."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM poh_quips WHERE status = 'kept'"
            " ORDER BY (last_mantra_at IS NOT NULL), last_mantra_at, RANDOM()"
            " LIMIT 1").fetchone()


def bump_mantra(quip_id: int) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE poh_quips SET mantra_count = mantra_count + 1,"
            " last_mantra_at = ?, updated_at = ? WHERE id = ?", (now, now, quip_id))


def quip_counts() -> dict[str, int]:
    with _connect() as conn:
        return {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM poh_quips GROUP BY status")}


# ── scan marks ────────────────────────────────────────────────────────────────

def mark_scanned(note_id: int, note_updated_at: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO poh_note_scans (note_id, scanned_updated_at,"
            " scanned_at) VALUES (?, ?, ?)", (note_id, note_updated_at, _now()))


def needs_scan(note_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT n.updated_at, s.scanned_updated_at FROM notes n"
            " LEFT JOIN poh_note_scans s ON s.note_id = n.id WHERE n.id = ?",
            (note_id,)).fetchone()
    if row is None:
        return False
    return row["scanned_updated_at"] is None or row["updated_at"] > row["scanned_updated_at"]


def notes_needing_scan(limit: int = 20) -> list[int]:
    with _connect() as conn:
        return [r["id"] for r in conn.execute(
            "SELECT n.id FROM notes n"
            " LEFT JOIN poh_note_scans s ON s.note_id = n.id"
            " WHERE n.status IN ('decoded', 'clarified')"
            "   AND (s.note_id IS NULL OR n.updated_at > s.scanned_updated_at)"
            " ORDER BY n.id LIMIT ?", (limit,))]
