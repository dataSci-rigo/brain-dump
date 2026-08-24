"""power-of-habit background loop (daemon thread started from main.py).

Responsibilities, all gated on DB state so restarts are idempotent:
  1. bootstrap the web taxonomy when empty
  2. morning mantra — one random kept quip, once per local day
  3. daily sweep — scan notes never scanned or edited since their last scan
  4. weekly web refresh of the taxonomy

Scheduled sends are Telegram-only in v1: telegram_frontend.send_message is a
module-level thread-safe sync call. (Immediate post-dump quips reach Discord
too, via the pipeline's OutText return path.)"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from outbound import OutText

from poh import scan, store, taxonomy

logger = logging.getLogger(__name__)


def _telegram():
    # Imported lazily: telegram_frontend imports pipeline, which imports poh —
    # a top-level import here would close that cycle during module init.
    from frontends import telegram_frontend
    return telegram_frontend

TICK_SECONDS = 300
SWEEP_BATCH = 20
REFRESH_DAYS = 7


def _send_out(out: OutText) -> str | None:
    keyboard = None
    if out.buttons:
        keyboard = {"inline_keyboard": [
            [{"text": b.label, "callback_data": b.callback_data[:64]} for b in row]
            for row in out.buttons]}
    msg_id = _telegram().send_message(out.text, reply_markup=keyboard)
    return str(msg_id) if msg_id else None


def _older_than_days(iso_ts: str, days: int) -> bool:
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - then > timedelta(days=days)


def _tick(now_local: datetime) -> None:
    today = now_local.date().isoformat()

    # 1. bootstrap when the taxonomy is empty
    if config.ANTHROPIC_API_KEY and not store.active_concepts():
        logger.info("poh: empty taxonomy — bootstrapping from the web")
        taxonomy.bootstrap()

    # 2. morning mantra
    if (now_local.hour >= config.POH_MORNING_HOUR
            and store.get_state("morning_mantra_date") != today):
        quip = store.pick_mantra_quip()
        if quip is None:
            store.set_state("morning_mantra_date", today)  # nothing kept yet
        else:
            msg_id = _telegram().send_message(f"🌅 {quip['quip']}")
            if msg_id:  # commit only on successful send; else retry next tick
                store.bump_mantra(quip["id"])
                store.set_state("morning_mantra_date", today)

    # 3. daily sweep
    if store.get_state("last_daily_sweep_date") != today:
        for note_id in store.notes_needing_scan(limit=SWEEP_BATCH):
            try:
                for out in scan.scan_note(note_id):
                    msg_id = _send_out(out)
                    if msg_id and out.buttons:
                        quip_id = out.buttons[0][0].callback_data.rsplit(":", 1)[-1]
                        if quip_id.isdigit():
                            store.set_quip_sent(int(quip_id), msg_id)
            except Exception:
                logger.exception("poh sweep failed for note %s", note_id)
        store.set_state("last_daily_sweep_date", today)

    # 4. weekly taxonomy refresh
    last = store.get_state("last_web_refresh_at")
    if config.ANTHROPIC_API_KEY and last and _older_than_days(last, REFRESH_DAYS):
        logger.info("poh: weekly taxonomy refresh")
        taxonomy.refresh()


def start() -> None:
    logger.info("poh scheduler starting (mantra hour %d, tz %s)",
                config.POH_MORNING_HOUR, config.TIMEZONE)
    tz = ZoneInfo(config.TIMEZONE)
    while True:
        try:
            _tick(datetime.now(tz))
        except Exception:
            logger.exception("poh tick failed")
        time.sleep(TICK_SECONDS)
