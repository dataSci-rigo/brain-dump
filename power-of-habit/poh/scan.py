"""Mine one note for whys / starting fears and turn each into a quip in the
user's own voice. One cheap forced-tool call per note (MODEL_FAST) — the
👍/👎 loop is the quality filter, and kept quips are injected as approved
style examples."""
from __future__ import annotations

import json
import logging

import config
import db
from ai import client
from outbound import Button, OutText

from poh import store

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["fear", "why"]},
                    "excerpt": {"type": "string",
                                "description": "The note text expressing it, verbatim."},
                    "matched_concept_id": {
                        "type": ["integer", "null"],
                        "description": "Taxonomy id it genuinely matches, else null."},
                    "new_concept_text": {
                        "type": ["string", "null"],
                        "description": "Short canonical phrasing for a personal"
                                       " concept not in the taxonomy (when no match)."},
                    "quip": {"type": "string",
                             "description": "One punchy line, <= 15 words, in the"
                                            " user's own voice."},
                },
                "required": ["kind", "excerpt", "quip"],
            },
        },
    },
    "required": ["findings"],
}

_SYSTEM = """\
You mine ONE personal brain-dump note for:
(a) WHYS — the user's real motivations behind things they want to do, and
(b) starting FEARS — hesitations/holdups that delay them from starting.

A reference taxonomy (id: text) is provided; set matched_concept_id when a
finding genuinely matches an entry, otherwise leave it null and set
new_concept_text with a short canonical phrasing of their personal concept.

For each finding write "quip": one punchy line (at most 15 words) in the
user's own voice and spirit — reuse their words where possible. For a WHY,
make it a rallying line. For a FEAR, name it so it loses power. The kept
quips listed show the voice the user has already approved.

Be conservative: most notes contain neither — return an empty findings list
unless a motivation or starting-fear is actually expressed. At most 2
findings."""


def _note_body(note) -> str:
    return " \n".join(filter(None, (note["title"], note["raw_text"],
                                    note["transcription"], note["interpretation"],
                                    note["enrichment"])))[:6000]


def scan_note(note_id: int) -> list[OutText]:
    """Scan one note; returns quip OutTexts (empty on no findings). AI failure
    returns [] and leaves the note unmarked so the daily sweep retries."""
    note = db.get_note(note_id)
    if note is None:
        return []

    payload = {
        "note": _note_body(note),
        "taxonomy": {
            "fears": [{"id": c["id"], "text": c["text"]}
                      for c in store.active_concepts("fear")],
            "whys": [{"id": c["id"], "text": c["text"]}
                     for c in store.active_concepts("why")],
        },
        "kept_quips": [q["quip"] for q in store.kept_quips(limit=10)],
    }
    result = client.call_tool(
        system=_SYSTEM,
        user_message=json.dumps(payload),
        tool_name="report_findings",
        tool_description="Report motivations (whys) and starting-fears found in"
                         " the note, each with a mantra-ready quip.",
        input_schema=_SCHEMA,
        model=config.MODEL_FAST,
    )
    if result is None:
        return []

    out: list[OutText] = []
    for finding in (result.get("findings") or [])[:2]:
        kind = finding.get("kind")
        quip = (finding.get("quip") or "").strip()
        if kind not in ("fear", "why") or not quip:
            continue

        concept_id = None
        matched = finding.get("matched_concept_id")
        if isinstance(matched, int) and store.get_concept(matched) is not None:
            concept_id = matched
        elif finding.get("new_concept_text"):
            concept_id = store.concept_id_for(kind, finding["new_concept_text"],
                                              "personal")

        quip_id = store.insert_quip(note_id=note_id, concept_id=concept_id,
                                    kind=kind, excerpt=finding.get("excerpt"),
                                    quip=quip)
        if quip_id is None:
            continue  # already quipped this concept on this note

        label = "starting fear" if kind == "fear" else "your why"
        emoji = "😨" if kind == "fear" else "🔥"
        out.append(OutText(
            f"{emoji} {quip}\n({label} from note #{note_id} — keep it?)",
            buttons=[[Button("👍 keep", f"p:k:{quip_id}"),
                      Button("👎 nah", f"p:x:{quip_id}")]],
        ))

    store.mark_scanned(note_id, note["updated_at"])
    return out


def handle_callback(data: str) -> tuple[str, str | None]:
    """p:k:<id> keep / p:x:<id> reject."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "p" or not parts[2].isdigit():
        return ("?", None)
    action, quip_id = parts[1], int(parts[2])
    quip = store.get_quip(quip_id)
    if quip is None:
        return ("Quip not found.", None)
    if quip["status"] != "pending":
        return ("Already handled.", None)

    if action == "k":
        store.set_quip_status(quip_id, "kept")
        return ("Kept ✓", f"🔥 {quip['quip']}\n✓ kept — you'll see this again")
    if action == "x":
        store.set_quip_status(quip_id, "rejected")
        return ("Dropped.", f"{quip['quip']}\n✖ rejected")
    return ("?", None)
