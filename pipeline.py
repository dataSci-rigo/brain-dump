"""Platform-agnostic capture pipeline. Synchronous; never sends messages —
returns OutText objects the frontends render. Save-first: the raw dump hits
the DB (and blob dir) before any AI call, so nothing is ever lost."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import config
import db
import poh
from ai import correct, decode, search_interp
from ai import client as ai_client
from integrations import compass, stm
from outbound import Button, OutText

logger = logging.getLogger(__name__)

COMMANDS = [
    ("search", "find notes and tasks: /search farmers market"),
    ("recent", "list recent notes: /recent [n]"),
    ("note", "show a note in full: /note 12"),
    ("help", "how this works"),
] + poh.COMMANDS

HELP_TEXT = (
    "Send me anything — a photo of a handwritten note, a screenshot, or plain "
    "text. I save it instantly, decode it, tag it, and only ask questions when "
    "something is truly unclear (you can ignore them; nothing is lost).\n\n"
    "Reply to my confirmation to correct me. Reply to my questions to answer "
    "them. Reply `retry` if decoding failed.\n\n"
    "/search <query> — find notes (and STM/Compass tasks)\n"
    "/recent [n] — latest notes\n"
    "/note <id> — full note detail"
)


@dataclass
class Inbound:
    platform: str                 # 'telegram' | 'discord'
    chat_id: str
    user_id: str | None = None
    message_id: str | None = None
    text: str | None = None
    image_bytes: bytes | None = None
    image_mime: str = "image/jpeg"
    reply_to_id: str | None = None


# ── top-level dispatch ────────────────────────────────────────────────────────

def handle_message(inbound: Inbound) -> list[OutText]:
    text = (inbound.text or "").strip()

    if text.startswith("/"):
        return _handle_command(inbound, text)

    if inbound.reply_to_id:
        replied = db.lookup_bot_message(inbound.platform, inbound.chat_id,
                                        inbound.reply_to_id)
        if replied is not None:
            return _handle_reply(inbound, replied["note_id"], replied["purpose"])

    # no reply-to: if we just asked questions in this chat, treat a plain text
    # message as the answers (chat_state fallback)
    if text and inbound.image_bytes is None:
        state = db.get_state(inbound.platform, inbound.chat_id)
        if state and state[0] == "awaiting_answers":
            note_id = state[1].get("note_id")
            if note_id and db.get_open_questions(note_id, unanswered_only=True):
                return _handle_reply(inbound, note_id, "questions")

    if text or inbound.image_bytes is not None:
        return handle_dump(inbound)
    return []


def _handle_command(inbound: Inbound, text: str) -> list[OutText]:
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lstrip("/").split("@")[0].lower()
    arg = arg.strip()
    if cmd == "help" or cmd == "start":
        return [OutText(HELP_TEXT)]
    if cmd == "search":
        if not arg:
            return [OutText("Usage: /search <what you're looking for>")]
        return handle_search(arg)
    if cmd == "recent":
        n = int(arg) if arg.isdigit() else 10
        rows = db.recent_notes(min(n, 25))
        if not rows:
            return [OutText("No notes yet — send me something!")]
        lines = [_note_line(r) for r in rows]
        return [OutText("Recent notes:\n" + "\n".join(lines))]
    if cmd == "note":
        if not arg.isdigit():
            return [OutText("Usage: /note <id>")]
        note = db.get_note(int(arg))
        if note is None:
            return [OutText(f"No note #{arg}.")]
        return [OutText(_format_note_full(note))]
    res = poh.handle_command(cmd, arg)
    if res is not None:
        return res
    return [OutText(f"Unknown command /{cmd}. Try /help.")]


# ── capture ───────────────────────────────────────────────────────────────────

def handle_dump(inbound: Inbound) -> list[OutText]:
    # 1. store raw immediately
    image_path = None
    if inbound.image_bytes is not None:
        ext = "png" if "png" in inbound.image_mime else "jpg"
        blob = config.BLOB_DIR / f"{uuid.uuid4().hex}.{ext}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(inbound.image_bytes)
        image_path = str(blob.relative_to(config.DATA_DIR))

    note_id = db.create_note(
        inbound.platform, inbound.chat_id, inbound.user_id, inbound.message_id,
        kind="photo" if inbound.image_bytes is not None else "text",
        raw_text=inbound.text, image_path=image_path,
    )
    return _decode_and_respond(note_id, inbound)


def _decode_and_respond(note_id: int, inbound: Inbound) -> list[OutText]:
    note = db.get_note(note_id)
    image_bytes = inbound.image_bytes
    if image_bytes is None and note["image_path"]:
        image_bytes = (config.DATA_DIR / note["image_path"]).read_bytes()

    # 2. decode: vision + web search, one request
    result, searched = decode.decode_dump(
        text=note["raw_text"], image_bytes=image_bytes,
        mime=inbound.image_mime, known_tags=db.get_distinct_tags(),
    )

    if result is None:
        return [OutText(
            f"Saved as note #{note_id}, but decoding failed — reply `retry` to "
            "this message to try again.",
            reply_to_id=inbound.message_id,
            record_purpose="confirmation", record_note_id=note_id,
        )]

    # 3. persist decode
    db.update_note(note_id, status="decoded",
                   title=result.get("title"),
                   transcription=result.get("transcription"),
                   interpretation=result.get("interpretation"),
                   model_used=config.MODEL_DECODE,
                   web_search_used=int(searched))
    db.set_tags(note_id, result.get("suggested_tags") or [])
    question_ids = db.add_open_questions(note_id, (result.get("open_questions") or [])[:3])
    item_ids = db.add_actionable_items(note_id, result.get("actionable_items") or [])

    out: list[OutText] = []

    # 4. confirmation
    tags = db.get_tags(note_id)
    confirmation = (
        f"📝 Saved #{note_id}: {result.get('title', 'untitled')}\n"
        f"{result.get('interpretation', '')}"
        + (f"\n🔎 (checked the web)" if searched else "")
        + (f"\n{' '.join('#' + t for t in tags)}" if tags else "")
        + "\n\nReply to this message to correct me."
    )
    out.append(OutText(confirmation, reply_to_id=inbound.message_id,
                       record_purpose="confirmation", record_note_id=note_id))

    # 5. questions (ignorable)
    questions = result.get("open_questions") or []
    if questions:
        q_text = "A couple of things I couldn't figure out (feel free to ignore):\n" + \
            "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions[:3]))
        out.append(OutText(q_text, record_purpose="questions", record_note_id=note_id))
        db.set_state(inbound.platform, inbound.chat_id, "awaiting_answers",
                     {"note_id": note_id, "question_ids": question_ids})

    # 6. actionable items
    for item_id in item_ids:
        item = db.get_actionable_item(item_id)
        target_hint = {"stm": " (suggest: STM)", "compass": " (suggest: Compass)"}.get(
            item["suggested_target"] or "", "")
        out.append(OutText(
            f"Task spotted{target_hint}:\n• {item['text']}",
            buttons=[[Button("→ STM", f"r:s:{item_id}"),
                      Button("→ Compass", f"r:c:{item_id}"),
                      Button("dismiss", f"r:d:{item_id}")]],
        ))

    if config.POH_ENABLED:
        try:
            out.extend(poh.scan_after_decode(note_id))
        except Exception:
            logger.exception("poh scan failed for note %s", note_id)
    return out


# ── replies: answers, corrections, retry ─────────────────────────────────────

_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "integer"},
                    "answer": {"type": "string"},
                },
                "required": ["question_id", "answer"],
            },
        },
    },
    "required": ["answers"],
}


def _handle_reply(inbound: Inbound, note_id: int, purpose: str) -> list[OutText]:
    text = (inbound.text or "").strip()
    note = db.get_note(note_id)
    if note is None:
        return [OutText("That note no longer exists.")]

    if text.lower() == "retry" and note["status"] == "raw":
        return _decode_and_respond(note_id, inbound)

    if purpose == "questions":
        return _handle_answers(inbound, note_id, text)
    return _handle_correction(inbound, note_id, text)


def _handle_answers(inbound: Inbound, note_id: int, text: str) -> list[OutText]:
    open_qs = db.get_open_questions(note_id, unanswered_only=True)
    if not open_qs:
        # nothing pending — treat as a correction/addendum instead
        return _handle_correction(inbound, note_id, text)

    payload = json.dumps({
        "questions": [{"question_id": q["id"], "question": q["question"]} for q in open_qs],
        "user_reply": text,
    })
    result = ai_client.call_tool(
        system=("Map the user's free-text reply onto the numbered open questions."
                " Only include questions the reply actually answers."),
        user_message=payload,
        tool_name="map_answers",
        tool_description="Match the reply to the open questions it answers.",
        input_schema=_ANSWER_SCHEMA,
        model=config.MODEL_FAST,
    )

    valid_ids = {q["id"]: q["question"] for q in open_qs}
    answered = []
    if result:
        for a in result.get("answers", []):
            qid = a.get("question_id")
            if qid in valid_ids and a.get("answer"):
                db.answer_question(qid, a["answer"])
                db.append_enrichment(note_id, f"Q: {valid_ids[qid]}\nA: {a['answer']}")
                answered.append(qid)
    if not answered:
        # AI couldn't map it (or failed) — keep the info anyway
        db.append_enrichment(note_id, f"Additional info: {text}")

    if not db.get_open_questions(note_id, unanswered_only=True):
        db.clear_state(inbound.platform, inbound.chat_id)
    db.update_note(note_id, status="clarified")
    return [OutText(f"Got it — note #{note_id} updated. ✅")]


def _handle_correction(inbound: Inbound, note_id: int, instruction: str) -> list[OutText]:
    note = db.get_note(note_id)
    result = correct.apply_correction(dict(note), db.get_tags(note_id), instruction)
    if result is None:
        # never lose the user's words
        db.append_enrichment(note_id, f"Correction (unprocessed): {instruction}")
        return [OutText("I couldn't process that correction right now, but I've "
                        f"saved your words on note #{note_id}.")]

    updates = {k: v for k, v in result.items()
               if k in ("title", "transcription", "interpretation") and v}
    if updates:
        db.update_note(note_id, **updates)
    if result.get("add_tags"):
        db.set_tags(note_id, result["add_tags"])
    if result.get("remove_tags"):
        db.remove_tags(note_id, result["remove_tags"])
    if result.get("note"):
        db.append_enrichment(note_id, result["note"])

    changed = list(updates) + \
        (["tags"] if result.get("add_tags") or result.get("remove_tags") else []) + \
        (["notes"] if result.get("note") else [])
    what = ", ".join(changed) if changed else "nothing (no change needed)"
    return [OutText(f"Note #{note_id} corrected — updated: {what}.")]


# ── callbacks (routing buttons) ──────────────────────────────────────────────

def handle_callback(data: str) -> tuple[str, str | None]:
    """Returns (ack/toast text, replacement text for the message or None)."""
    if data.startswith("p:"):
        return poh.handle_callback(data)
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "r":
        return ("?", None)
    action, item_id_s = parts[1], parts[2]
    if not item_id_s.isdigit():
        return ("?", None)
    item = db.get_actionable_item(int(item_id_s))
    if item is None:
        return ("Item not found.", None)
    if item["status"] != "pending":
        return ("Already handled.", None)

    note = db.get_note(item["note_id"])
    context = f"From brain-dump note #{item['note_id']}" + \
        (f" ({note['title']})" if note and note["title"] else "")

    if action == "d":
        db.update_actionable_item(item["id"], status="dismissed")
        return ("Dismissed.", f"• {item['text']}\n✖ dismissed")

    if action == "s":
        task_id = stm.route_to_stm(item["text"], context)
        if task_id is None:
            return ("STM routing failed — item kept.", None)
        db.update_actionable_item(item["id"], status="routed_stm", routed_id=str(task_id))
        return (f"Sent to STM (task {task_id}).", f"• {item['text']}\n→ STM task #{task_id}")

    if action == "c":
        task_id = compass.route_to_compass(item["text"])
        if task_id is None:
            return ("Compass routing failed — item kept.", None)
        db.update_actionable_item(item["id"], status="routed_compass", routed_id=task_id)
        return (f"Sent to Compass (task {task_id}).",
                f"• {item['text']}\n→ Compass task #{task_id}")

    return ("?", None)


# ── search ────────────────────────────────────────────────────────────────────

def handle_search(query: str) -> list[OutText]:
    today = datetime.now(timezone.utc).date().isoformat()
    spec = search_interp.interpret_query(query, db.get_distinct_tags(), today)
    if spec is None:
        spec = {"fts_query": query, "tags": [], "since": None, "include_tasks": True}

    notes = db.search_notes(spec.get("fts_query") or query,
                            tags=spec.get("tags") or [],
                            since=spec.get("since"))
    terms = [t for t in (spec.get("fts_query") or query).replace(" OR ", " ").split()
             if len(t) > 2][:5]

    sections = []
    if notes:
        sections.append("📝 Notes:\n" + "\n".join(_note_line(n) for n in notes))
    if spec.get("include_tasks", True):
        stm_hits = stm.search_stm(terms)
        if stm_hits:
            sections.append("☑️ STM tasks:\n" + "\n".join(
                f"  #{t['id']} {t['title']} ({t['status']})" for t in stm_hits))
        compass_hits = compass.search_compass(terms)
        if compass_hits:
            sections.append("🧭 Compass tasks:\n" + "\n".join(
                f"  #{t['id']} {t['title']} ({t['status']})" for t in compass_hits))

    if not sections:
        return [OutText(f"Nothing found for “{query}”.")]
    return [OutText("\n\n".join(sections))]


# ── formatting ────────────────────────────────────────────────────────────────

def _note_line(note) -> str:
    tags = db.get_tags(note["id"])
    date = (note["created_at"] or "")[:10]
    title = note["title"] or (note["raw_text"] or "")[:40] or f"({note['kind']})"
    return f"  #{note['id']} {title} — {date}" + \
        (f" [{' '.join(tags)}]" if tags else "")


def _format_note_full(note) -> str:
    parts = [f"📝 Note #{note['id']} — {note['title'] or 'untitled'}",
             f"({note['kind']}, {note['status']}, {(note['created_at'] or '')[:16]})"]
    if note["transcription"]:
        parts.append(f"\nTranscription:\n{note['transcription']}")
    if note["interpretation"]:
        parts.append(f"\nMeaning:\n{note['interpretation']}")
    if note["enrichment"]:
        parts.append(f"\nAdditions:\n{note['enrichment']}")
    tags = db.get_tags(note["id"])
    if tags:
        parts.append("\n" + " ".join("#" + t for t in tags))
    qs = db.get_open_questions(note["id"])
    unanswered = [q for q in qs if not q["answer"]]
    if unanswered:
        parts.append("\nOpen questions:\n" + "\n".join(
            f"  • {q['question']}" for q in unanswered))
    return "\n".join(parts)
