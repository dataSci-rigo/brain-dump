"""Decode a brain dump (photo or text) in ONE request: vision + web search.

The web_search server tool runs inside the same call — no forced tool_choice
(that would block searching), so the JSON contract is enforced by the system
prompt and parsed with the fence-stripping parser (food/recipe_ingest.py
lineage)."""
from __future__ import annotations

import base64

import config
from ai import client

_SYSTEM = """\
You decode a user's brain-dump for an ADHD-friendly note-capture system. The
input is a photo (often messy handwriting: sticky notes, whiteboards, scraps)
or free text. Your job:

1. TRANSCRIBE verbatim. Mark anything illegible as [?]. Preserve line structure.
2. INTERPRET: what does this note mean / what is it for? Expand abbreviations,
   fix obvious slips, structure lists. Be concrete and brief.
3. WEB SEARCH (optional, max 3 searches): only when a real-world entity in the
   note is unclear or worth verifying — a place, event, product, schedule
   (e.g. a farmers market's actual days/hours). Fold findings into the
   interpretation. Skip searching when the note is self-contained.
4. QUESTIONS: at most 3 clarifying questions, ONLY for things neither the note
   nor the web resolves. Usually zero. Never interrogate.
5. ACTIONABLE ITEMS: concrete to-dos implied by the note, if any.
   suggested_target: "stm" for one-off tasks/errands, "compass" for
   goal/role/project-level work, null if unsure.

Existing tags to prefer reusing: {known_tags}

Reply with ONLY one JSON object (no prose, no markdown fences):
{{
  "title": "short note title",
  "transcription": "verbatim text, [?] for illegible",
  "interpretation": "what it means, enriched with any web findings",
  "suggested_tags": ["lowercase-kebab", "3 to 6 of them"],
  "entities": ["real-world things mentioned"],
  "open_questions": ["max 3, usually none"],
  "actionable_items": [{{"text": "...", "suggested_target": "stm"|"compass"|null}}],
  "confidence": "high"|"medium"|"low"
}}"""

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


def decode_dump(*, text: str | None = None, image_bytes: bytes | None = None,
                mime: str = "image/jpeg", known_tags: list[str] | None = None,
                ) -> tuple[dict | None, bool]:
    """Returns (decode_result | None, web_search_used)."""
    system = _SYSTEM.format(known_tags=", ".join(known_tags or []) or "(none yet)")
    if image_bytes is not None:
        content: list | str = [
            {"type": "image",
             "source": {"type": "base64", "media_type": mime,
                        "data": base64.standard_b64encode(image_bytes).decode()}},
            {"type": "text",
             "text": ("Decode this brain-dump photo."
                      + (f" Caption from the user: {text}" if text else ""))},
        ]
    else:
        content = f"Decode this brain-dump:\n\n{text}"
    return client.call_json(system=system, content=content,
                            model=config.MODEL_DECODE,
                            server_tools=[_WEB_SEARCH_TOOL])
