# brain-dump

AI-aided brain-dump / note capture for ADHD. Send a photo of a handwritten
note (or plain text) via **Telegram** or **Discord**; the bot:

1. **Saves it instantly** (raw text + image blob) — nothing is ever lost.
2. **Decodes it** with the Anthropic API (vision), using the **web search
   server tool** in the same request to verify real-world details (e.g. a
   farmers market's actual days/hours).
3. **Asks at most 3 clarifying questions** — only for what neither the note
   nor the web resolves. Ignorable forever.
4. **Tags and indexes** the note (FTS5) for later `/search`.
5. **Routes actionable items** by button into the Semantic Task Manager
   (direct SQLite insert, `source='braindump'`) or Compass/hab7bot
   (`POST /api/v1/capture` with `X-Api-Key`).

Search federates across all three systems: brain-dump notes, STM tasks,
Compass tasks.

## Layout

- `main.py` — entry; Discord owns the asyncio loop, Telegram sync poller in a daemon thread (Telegram-only if no Discord token)
- `pipeline.py` — platform-agnostic capture/reply/callback/search logic
- `db.py` — SQLite (WAL, busy_timeout) + FTS5; `data/notes.db`, images in `data/blobs/`
- `ai/` — decode (opus + web_search), search interpretation + reply mapping (haiku), corrections
- `frontends/` — telegram (sync requests long-poll), discord (discord.py)
- `integrations/` — stm.py, compass.py

## Env (via `env_sync.py sync` from the master `.env`)

`BRAINDUMP_TELEGRAM_TOKEN`, `BRAINDUMP_TELEGRAM_CHAT_ID`,
`BRAINDUMP_DISCORD_TOKEN` (optional), `BRAINDUMP_DISCORD_CHANNEL_IDS`
(optional csv; DMs always work), `DISCORD_OWNER_ID`,
`BRAINDUMP_MODEL_DECODE`, `BRAINDUMP_MODEL_FAST`, `ANTHROPIC_API_KEY`
(global), `STM_DB_PATH`, `COMPASS_DB_PATH`, `COMPASS_API_URL`,
`HAB7BOT_INTERNAL_API_KEY`.

## Run

```bash
conda activate p312   # or the VM venv
python main.py
```

Deployed as systemd unit `app-braindump` on the VM (see `env_sync.py`
`_SERVICE_REGISTRY`). The Telegram identity is the former ADHD bot's token —
**app-adhd must stay stopped** or both processes long-poll the same token and
Telegram 409s one of them silently.

Note: `data/blobs/` (original images) is not in the GCS backup map — only
`notes.db` is.
