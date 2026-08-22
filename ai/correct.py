"""Apply a free-text correction to a stored note. The note is always resolved
by id from the bot_messages row the user replied to — never by display
position (STM's handle_correction position-vs-id bug is the cautionary tale)."""
from __future__ import annotations

import json

import config
from ai import client

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "transcription": {"type": ["string", "null"]},
        "interpretation": {"type": ["string", "null"]},
        "add_tags": {"type": "array", "items": {"type": "string"}},
        "remove_tags": {"type": "array", "items": {"type": "string"}},
        "note": {"type": ["string", "null"],
                 "description": "Extra context to append to the note, if the"
                                " instruction adds info rather than fixing fields."},
    },
    "required": ["add_tags", "remove_tags"],
}


def apply_correction(note: dict, tags: list[str], instruction: str) -> dict | None:
    """Returns field updates (None-valued fields = unchanged)."""
    payload = {
        "note": {k: note.get(k) for k in
                 ("title", "transcription", "interpretation", "enrichment")},
        "current_tags": tags,
        "user_instruction": instruction,
    }
    return client.call_tool(
        system=("The user is correcting a stored note. Return ONLY the fields"
                " that should change; leave everything else null/empty."
                " Corrections override the AI's earlier reading — trust the user."),
        user_message=json.dumps(payload),
        tool_name="correct_note",
        tool_description="Apply the user's correction to the note.",
        input_schema=_SCHEMA,
        model=config.MODEL_DECODE,
    )
