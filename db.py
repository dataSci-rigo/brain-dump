"""SQLite note store + FTS5 index + chat state. Every function opens its own
connection (house pattern from food/db.py & STM db.py) — inherently thread-safe
across the Telegram poller thread and Discord's to_thread workers."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    platform            TEXT NOT NULL,
    chat_id             TEXT NOT NULL,
    user_id             TEXT,
    inbound_message_id  TEXT,
    kind                TEXT NOT NULL,
    raw_text            TEXT,
    image_path          TEXT,
    title               TEXT,
    transcription       TEXT,
    interpretation      TEXT,
    enrichment          TEXT,
    status              TEXT NOT NULL DEFAULT 'raw',
    model_used          TEXT,
    web_search_used     INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    note_id  INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    body, tags, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS actionable_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id          INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    text             TEXT NOT NULL,
    suggested_target TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    routed_id        TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS open_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    question    TEXT NOT NULL,
    answer      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_messages (
    platform    TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    note_id     INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (platform, chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS chat_state (
    platform     TEXT NOT NULL,
    chat_id      TEXT NOT NULL,
    state        TEXT NOT NULL,
    pending_json TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (platform, chat_id)
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
    config.BLOB_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ── notes ─────────────────────────────────────────────────────────────────────

def create_note(platform: str, chat_id: str, user_id: str | None,
                inbound_message_id: str | None, kind: str,
                raw_text: str | None = None, image_path: str | None = None) -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes (platform, chat_id, user_id, inbound_message_id, kind,"
            " raw_text, image_path, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (platform, chat_id, user_id, inbound_message_id, kind,
             raw_text, image_path, now, now),
        )
        note_id = cur.lastrowid
    reindex_note(note_id)
    return note_id


def get_note(note_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()


def update_note(note_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE notes SET {sets} WHERE id = ?",
                     (*fields.values(), note_id))
    reindex_note(note_id)


def append_enrichment(note_id: int, text: str) -> None:
    with _connect() as conn:
        row = conn.execute("SELECT enrichment FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return
        combined = (row["enrichment"] + "\n" if row["enrichment"] else "") + text
        conn.execute("UPDATE notes SET enrichment = ?, updated_at = ? WHERE id = ?",
                     (combined, _now(), note_id))
    reindex_note(note_id)


def recent_notes(limit: int = 10) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ── tags ──────────────────────────────────────────────────────────────────────

def set_tags(note_id: int, tags: list[str], replace: bool = False) -> None:
    clean = {t.strip().lower().replace(" ", "-") for t in tags if t.strip()}
    with _connect() as conn:
        if replace:
            conn.execute("DELETE FROM tags WHERE note_id = ?", (note_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?, ?)",
            [(note_id, t) for t in clean])
    reindex_note(note_id)


def remove_tags(note_id: int, tags: list[str]) -> None:
    with _connect() as conn:
        conn.executemany("DELETE FROM tags WHERE note_id = ? AND tag = ?",
                         [(note_id, t.strip().lower()) for t in tags])
    reindex_note(note_id)


def get_tags(note_id: int) -> list[str]:
    with _connect() as conn:
        return [r["tag"] for r in conn.execute(
            "SELECT tag FROM tags WHERE note_id = ? ORDER BY tag", (note_id,))]


def get_distinct_tags() -> list[str]:
    with _connect() as conn:
        return [r["tag"] for r in conn.execute(
            "SELECT DISTINCT tag FROM tags ORDER BY tag")]


# ── FTS ───────────────────────────────────────────────────────────────────────

def reindex_note(note_id: int) -> None:
    with _connect() as conn:
        note = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if note is None:
            conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
            return
        body = " ".join(filter(None, (note["title"], note["raw_text"],
                                      note["transcription"], note["interpretation"],
                                      note["enrichment"])))
        tag_row = conn.execute(
            "SELECT group_concat(tag, ' ') AS t FROM tags WHERE note_id = ?",
            (note_id,)).fetchone()
        conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        conn.execute("INSERT INTO notes_fts (rowid, body, tags) VALUES (?, ?, ?)",
                     (note_id, body, tag_row["t"] or ""))


def search_notes(fts_query: str, tags: list[str] | None = None,
                 since: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
    """FTS5 match ranked by bm25; falls back to a quoted-phrase query if the
    model-generated FTS syntax is invalid."""
    sql = ("SELECT n.*, bm25(notes_fts) AS rank FROM notes_fts"
           " JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ?")
    params: list = [fts_query]
    if since:
        sql += " AND n.created_at >= ?"
        params.append(since)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            quoted = '"' + fts_query.replace('"', "") + '"'
            params[0] = quoted
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = []
    if tags:
        tagged = set()
        with _connect() as conn:
            for r in conn.execute(
                    f"SELECT DISTINCT note_id FROM tags WHERE tag IN ({','.join('?' * len(tags))})",
                    [t.lower() for t in tags]):
                tagged.add(r["note_id"])
        # boost tag matches to the front rather than hard-filtering
        rows = sorted(rows, key=lambda r: (r["id"] not in tagged, r["rank"]))
    return rows


# ── actionable items / open questions ────────────────────────────────────────

def add_actionable_items(note_id: int, items: list[dict]) -> list[int]:
    now = _now()
    ids = []
    with _connect() as conn:
        for item in items:
            cur = conn.execute(
                "INSERT INTO actionable_items (note_id, text, suggested_target,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (note_id, item["text"], item.get("suggested_target"), now, now))
            ids.append(cur.lastrowid)
    return ids


def get_actionable_item(item_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM actionable_items WHERE id = ?",
                            (item_id,)).fetchone()


def update_actionable_item(item_id: int, **fields) -> None:
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE actionable_items SET {sets} WHERE id = ?",
                     (*fields.values(), item_id))


def add_open_questions(note_id: int, questions: list[str]) -> list[int]:
    now = _now()
    ids = []
    with _connect() as conn:
        for q in questions:
            cur = conn.execute(
                "INSERT INTO open_questions (note_id, question, created_at)"
                " VALUES (?, ?, ?)", (note_id, q, now))
            ids.append(cur.lastrowid)
    return ids


def get_open_questions(note_id: int, unanswered_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM open_questions WHERE note_id = ?"
    if unanswered_only:
        sql += " AND answer IS NULL"
    with _connect() as conn:
        return conn.execute(sql + " ORDER BY id", (note_id,)).fetchall()


def answer_question(question_id: int, answer: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE open_questions SET answer = ? WHERE id = ?",
                     (answer, question_id))


# ── bot message map / chat state ─────────────────────────────────────────────

def record_bot_message(platform: str, chat_id: str, message_id: str,
                       note_id: int, purpose: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_messages (platform, chat_id, message_id,"
            " note_id, purpose, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (platform, chat_id, message_id, note_id, purpose, _now()))


def lookup_bot_message(platform: str, chat_id: str, message_id: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM bot_messages WHERE platform = ? AND chat_id = ?"
            " AND message_id = ?", (platform, chat_id, message_id)).fetchone()


def set_state(platform: str, chat_id: str, state: str, pending: dict | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chat_state (platform, chat_id, state,"
            " pending_json, updated_at) VALUES (?, ?, ?, ?, ?)",
            (platform, chat_id, state, json.dumps(pending or {}), _now()))


def get_state(platform: str, chat_id: str) -> tuple[str, dict] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM chat_state WHERE platform = ? AND chat_id = ?",
            (platform, chat_id)).fetchone()
    if row is None:
        return None
    return row["state"], json.loads(row["pending_json"] or "{}")


def clear_state(platform: str, chat_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_state WHERE platform = ? AND chat_id = ?",
                     (platform, chat_id))
