"""brain-dump entry point.

Discord (asyncio) owns the main thread when a token is configured; the
Telegram sync poller runs in a daemon thread. With no Discord token, the
Telegram poller runs in the main thread. Either frontend failing doesn't take
down the other."""
from __future__ import annotations

import logging
import sys
import threading

import config
import db
from frontends import telegram_frontend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("brain-dump")


def main() -> None:
    if not config.TELEGRAM_TOKEN:
        logger.error("BRAINDUMP_TELEGRAM_TOKEN not set — nothing to run.")
        sys.exit(1)
    if not config.TELEGRAM_CHAT_ID:
        logger.error("BRAINDUMP_TELEGRAM_CHAT_ID not set.")
        sys.exit(1)
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — dumps will save raw, no decoding.")

    db.init()
    logger.info("DB ready at %s", config.DB_PATH)

    if config.POH_ENABLED:
        import poh
        poh.init_db()
        threading.Thread(target=poh.start, name="poh-scheduler",
                         daemon=True).start()
        logger.info("power-of-habit scheduler started")
    logger.info("STM integration: %s", config.STM_DB_PATH or "off")
    logger.info("Compass integration: %s / %s",
                config.COMPASS_API_URL, config.COMPASS_DB_PATH or "no local read")

    if config.DISCORD_TOKEN:
        threading.Thread(target=telegram_frontend.run_polling,
                         name="telegram-poller", daemon=True).start()
        from frontends import discord_frontend  # import here: discord.py optional
        discord_frontend.run()
    else:
        logger.info("No BRAINDUMP_DISCORD_TOKEN — running Telegram-only.")
        telegram_frontend.run_polling()


if __name__ == "__main__":
    main()
