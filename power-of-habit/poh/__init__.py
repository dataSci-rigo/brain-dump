"""power-of-habit: mines brain dumps for the user's whys and starting fears,
rewrites them as quips (👍 keeps / 👎 rejects), bootstraps + refreshes a
common-fears/whys taxonomy from the web, and re-sends kept quips as morning
mantras. Public surface used by brain-dump's pipeline.py and main.py."""
from __future__ import annotations

from outbound import OutText

from poh.commands import COMMANDS, handle_command
from poh.scan import handle_callback
from poh.scheduler import start
from poh.store import init as init_db

import config

from poh import scan, store


def scan_after_decode(note_id: int) -> list[OutText]:
    """Immediate post-decode quip scan. Cheap fast-model call; degrades to []
    on any AI failure (the note stays unmarked so the daily sweep retries)."""
    if not config.POH_ENABLED or not store.needs_scan(note_id):
        return []
    return scan.scan_note(note_id)


__all__ = ["COMMANDS", "handle_command", "handle_callback", "start",
           "init_db", "scan_after_decode"]
