"""Offline smoke test for power-of-habit: bootstrap -> scan -> callbacks ->
scheduler idempotency -> commands, with the AI layer stubbed and a temp DB.
Run: python power-of-habit/smoke_test.py (from any cwd)."""
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config

tmp = pathlib.Path(tempfile.mkdtemp())
config.DATA_DIR = tmp
config.BLOB_DIR = tmp / "blobs"
config.DB_PATH = tmp / "notes.db"
config.POH_ENABLED = True
config.POH_MORNING_HOUR = 8

import db
import pipeline
import poh
from poh import scan, scheduler, store, taxonomy
from ai import client as ai_client

db.init()
poh.init_db()

with store._connect() as conn:
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
assert {"poh_concepts", "poh_quips", "poh_note_scans", "poh_state",
        "poh_quip_note_concept"} <= tables
print("schema OK")

# ── bootstrap + refresh ──────────────────────────────────────────────────────
FEARS = [f"fear {i}" for i in range(20)]
WHYS = ["family", "mastery", "freedom"]
taxonomy.client.call_json = lambda **kw: ({"fears": FEARS, "whys": WHYS}, True)

assert taxonomy.bootstrap()
assert len(store.active_concepts("fear")) == 20
assert len(store.active_concepts("why")) == 3
taxonomy.bootstrap()
assert len(store.active_concepts()) == 23  # UNIQUE dedupe
print("bootstrap OK")

taxonomy.client.call_json = lambda **kw: (
    {"fears": ["fear 0", "fear 1", "brand new fear"], "whys": []}, True)
assert taxonomy.refresh()
assert len(store.active_concepts("fear")) == 21  # exactly 1 new
assert store.get_state("last_web_refresh_at")
print("refresh dedupe OK")

# ── immediate scan path ──────────────────────────────────────────────────────
note_id = db.create_note("telegram", "123", "u1", "10", "text",
                         raw_text="I want to get fit so I can keep up with my kids,"
                                  " but I keep waiting for the perfect plan")
db.update_note(note_id, status="decoded", title="Fitness thought",
               interpretation="Wants fitness for family; perfectionism delays start.")

matched_fear = store.active_concepts("fear")[0]["id"]
FINDINGS = {"findings": [
    {"kind": "fear", "excerpt": "waiting for the perfect plan",
     "matched_concept_id": matched_fear, "new_concept_text": None,
     "quip": "The perfect plan is the trap. Start ugly."},
    {"kind": "why", "excerpt": "keep up with my kids",
     "matched_concept_id": None, "new_concept_text": "keeping up with my kids",
     "quip": "Get fit to keep up with my kids."},
]}
scan.client.call_tool = lambda **kw: FINDINGS

outs = poh.scan_after_decode(note_id)
assert len(outs) == 2, [o.text for o in outs]
assert "😨" in outs[0].text and outs[0].buttons[0][0].callback_data.startswith("p:k:")
assert "🔥" in outs[1].text and outs[1].buttons[0][1].callback_data.startswith("p:x:")
personal = [c for c in store.active_concepts("why") if c["source"] == "personal"]
assert len(personal) == 1 and personal[0]["text"] == "keeping up with my kids"
assert not store.needs_scan(note_id)
assert poh.scan_after_decode(note_id) == []  # no rescan while unchanged
print("immediate scan OK")

# edit → rescan queued, but concept+note unique index blocks duplicate quips
db.update_note(note_id, interpretation="changed")
assert store.needs_scan(note_id)
assert note_id in store.notes_needing_scan()
outs = poh.scan_after_decode(note_id)
assert outs == [], [o.text for o in outs]  # same findings, all deduped
assert not store.needs_scan(note_id)
print("edit rescan + quip dedupe OK")

# ── callbacks via pipeline (routing + no collision) ──────────────────────────
ack, repl = pipeline.handle_callback("p:k:1")
assert ack == "Kept ✓" and "kept" in repl
assert store.get_quip(1)["status"] == "kept"
ack, _ = pipeline.handle_callback("p:k:1")
assert ack == "Already handled."
ack, repl = pipeline.handle_callback("p:x:2")
assert ack == "Dropped." and store.get_quip(2)["status"] == "rejected"
ack, _ = pipeline.handle_callback("r:d:999")
assert ack == "Item not found."  # old r:* path untouched
print("callbacks OK")

# ── AI failure degrades, note not marked ─────────────────────────────────────
note2 = db.create_note("telegram", "123", "u1", "11", "text", raw_text="fail case")
db.update_note(note2, status="decoded")
scan.client.call_tool = lambda **kw: None
assert poh.scan_after_decode(note2) == []
assert store.needs_scan(note2)  # sweep will retry
print("AI-failure degradation OK")

# ── scheduler idempotency ────────────────────────────────────────────────────
sent = []
class _TG:
    @staticmethod
    def send_message(text, reply_to=None, reply_markup=None):
        sent.append(text)
        return 111
scheduler._telegram = lambda: _TG

refresh_calls = []
scheduler.taxonomy.refresh = lambda: refresh_calls.append(1) or True
scan.client.call_tool = lambda **kw: {"findings": []}

nine_am = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
scheduler._tick(nine_am)
scheduler._tick(nine_am)
mantras = [s for s in sent if s.startswith("🌅")]
assert len(mantras) == 1, mantras                       # one mantra despite two ticks
assert store.get_quip(1)["mantra_count"] == 1
assert store.get_state("last_daily_sweep_date") == "2026-08-22"
assert not store.needs_scan(note2)                      # sweep caught the retry
print("scheduler idempotency OK")

# failed mantra send leaves the date unset (retry next tick)
store.set_state("morning_mantra_date", "2000-01-01")
class _TGFail:
    @staticmethod
    def send_message(text, reply_to=None, reply_markup=None):
        return None
scheduler._telegram = lambda: _TGFail
scheduler._tick(nine_am)
assert store.get_state("morning_mantra_date") == "2000-01-01"
print("mantra send-failure retry OK")

# stale refresh stamp triggers exactly one refresh
old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
store.set_state("last_web_refresh_at", old)
scheduler._telegram = lambda: _TG
scheduler._tick(nine_am)
assert len(refresh_calls) == 1, refresh_calls
print("weekly refresh trigger OK")

# ── commands ─────────────────────────────────────────────────────────────────
def cmd(text):
    return pipeline.handle_message(pipeline.Inbound(
        platform="telegram", chat_id="123", text=text))

assert "Kept quips" in cmd("/quips")[0].text
assert "Starting fears" in cmd("/fears")[0].text and "fear 0" in cmd("/fears")[0].text
assert "keeping up with my kids" in cmd("/whys")[0].text
assert "Power of Habit status" in cmd("/poh")[0].text
assert "Unknown command" in cmd("/zzz")[0].text
assert any(c[0] == "quips" for c in pipeline.COMMANDS)
print("commands OK")

print("ALL POH SMOKE TESTS PASSED")
