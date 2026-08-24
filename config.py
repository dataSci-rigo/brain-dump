"""Env + paths for brain-dump. All model IDs live here — never at call sites."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
BLOB_DIR = DATA_DIR / "blobs"
DB_PATH = DATA_DIR / "notes.db"

# power-of-habit/ can't be a package name (hyphen); the poh package lives
# inside it and this shim makes `import poh` work everywhere.
sys.path.insert(0, str(PROJECT_DIR / "power-of-habit"))


def _load_env() -> None:
    """Load project .env, then parent master .env for shared keys
    (same layering as food/config.py — project keys win)."""
    for env_path in (PROJECT_DIR.parent / ".env", PROJECT_DIR / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip().strip("'\"")


_load_env()

TELEGRAM_TOKEN = os.environ.get("BRAINDUMP_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.environ.get("BRAINDUMP_TELEGRAM_CHAT_ID", "0") or 0)

DISCORD_TOKEN = os.environ.get("BRAINDUMP_DISCORD_TOKEN", "")
DISCORD_CHANNEL_IDS = {
    int(x) for x in os.environ.get("BRAINDUMP_DISCORD_CHANNEL_IDS", "").split(",") if x.strip()
}
DISCORD_OWNER_ID = int(os.environ.get("DISCORD_OWNER_ID", "0") or 0)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_DECODE = os.environ.get("BRAINDUMP_MODEL_DECODE", "claude-opus-5")
MODEL_FAST = os.environ.get("BRAINDUMP_MODEL_FAST", "claude-haiku-4-5")


def _db_path(env_key: str, vm_default: str, local_fallback: str) -> Path | None:
    """Resolve an integration DB path: env override, else VM path, else local
    dev fallback under ~/Documents. None if nothing exists (integration off)."""
    for candidate in (os.environ.get(env_key, ""), vm_default, local_fallback):
        if candidate and Path(candidate).expanduser().exists():
            return Path(candidate).expanduser()
    return None


STM_DB_PATH = _db_path(
    "STM_DB_PATH",
    "~/apps/semantic_task_manager/data/tasks.db",
    "~/Documents/semantic_task_manager/data/tasks.db",
)
COMPASS_DB_PATH = _db_path(
    "COMPASS_DB_PATH",
    "~/apps/hab7bot/backend/data/compass.db",
    "~/Documents/hab7bot/backend/data/compass.db",
)
COMPASS_API_URL = os.environ.get("COMPASS_API_URL", "http://localhost:8010")
COMPASS_API_KEY = os.environ.get("HAB7BOT_INTERNAL_API_KEY", "")

TIMEZONE = os.environ.get("TIMEZONE", "America/Los_Angeles")

# power-of-habit quip miner
POH_ENABLED = os.environ.get("POH_ENABLED", "1").lower() not in ("0", "false", "")
POH_MORNING_HOUR = int(os.environ.get("POH_MORNING_HOUR", "8") or 8)
