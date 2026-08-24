"""/quips /fears /whys /poh command handlers. handle_command returns None for
commands it doesn't own so pipeline's unknown-command fallback still fires."""
from __future__ import annotations

import config
from outbound import OutText

from poh import store

COMMANDS = [
    ("quips", "kept mantra quips"),
    ("fears", "starting-fear taxonomy + yours"),
    ("whys", "your whys"),
    ("poh", "power-of-habit status"),
]


def _concept_list(kind: str, heading: str) -> str:
    concepts = store.active_concepts(kind)
    if not concepts:
        return f"{heading}: nothing yet — the taxonomy bootstraps itself shortly."
    web = [c["text"] for c in concepts if c["source"] == "web"]
    personal = [c["text"] for c in concepts if c["source"] == "personal"]
    parts = [heading + ":"]
    if personal:
        parts.append("Yours:\n" + "\n".join(f"  • {t}" for t in personal))
    if web:
        parts.append("From the research:\n" + "\n".join(f"  • {t}" for t in web))
    return "\n\n".join(parts)


def handle_command(cmd: str, arg: str) -> list[OutText] | None:
    if cmd == "quips":
        kept = store.kept_quips()
        if not kept:
            return [OutText("No kept quips yet — 👍 the ones I send you.")]
        lines = [f"  #{q['id']} {q['quip']}" for q in kept]
        return [OutText("🔥 Kept quips:\n" + "\n".join(lines))]

    if cmd == "fears":
        return [OutText(_concept_list("fear", "😨 Starting fears"))]

    if cmd == "whys":
        return [OutText(_concept_list("why", "🔥 Whys"))]

    if cmd == "poh":
        fears = store.active_concepts("fear")
        whys = store.active_concepts("why")
        counts = store.quip_counts()
        text = (
            "Power of Habit status\n"
            f"Concepts: {len(fears)} fears ({sum(1 for c in fears if c['source'] == 'personal')} yours), "
            f"{len(whys)} whys ({sum(1 for c in whys if c['source'] == 'personal')} yours)\n"
            f"Quips: {counts.get('kept', 0)} kept, {counts.get('pending', 0)} pending, "
            f"{counts.get('rejected', 0)} rejected\n"
            f"Notes awaiting scan: {len(store.notes_needing_scan(limit=999))}\n"
            f"Last web refresh: {store.get_state('last_web_refresh_at') or 'never'}\n"
            f"Last daily sweep: {store.get_state('last_daily_sweep_date') or 'never'}\n"
            f"Morning mantra sent: {store.get_state('morning_mantra_date') or 'never'} "
            f"(hour {config.POH_MORNING_HOUR})\n"
            f"Enabled: {config.POH_ENABLED}"
        )
        return [OutText(text)]

    return None
