"""Reference taxonomy of common starting fears and whys, researched from the
web via the Anthropic web_search server tool (same tool literal as
ai/decode.py). Bootstrap fills an empty taxonomy; refresh() runs weekly and
asks only for genuinely NEW concepts — UNIQUE(kind, text) is the hard dedupe
backstop behind the model's semantic dedupe."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
from ai import client

from poh import store

logger = logging.getLogger(__name__)

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}

_SYSTEM = """\
You are building a reference taxonomy for a personal anti-procrastination
coach. Use web_search (up to 5 searches) to research:

1. FEARS: the most commonly cited fears/holdups that delay people from
   STARTING a task — procrastination "starting fears" (e.g. fear of failure,
   perfectionism, task feels too big, unclear first step, fear of judgment).
   Compile the 20 most common.
2. WHYS: the most commonly cited deep motivations ("whys") people have for
   their goals (e.g. providing for family, mastery, autonomy, health,
   freedom, being a role model). Compile 10-15.

Phrase each as a short canonical concept, at most 10 words, no numbering,
no trailing periods.
{extra}
Reply with ONLY one JSON object (no prose, no markdown fences):
{{"fears": ["..."], "whys": ["..."]}}"""

_REFRESH_EXTRA = """
The taxonomy already contains the concepts below. Return ONLY genuinely NEW
concepts that are not present in — and not semantic duplicates of — these
lists. Empty arrays are a fine answer.

Existing fears: {fears}
Existing whys: {whys}
"""


def _run(extra: str) -> bool:
    result, _ = client.call_json(
        system=_SYSTEM.format(extra=extra),
        content="Research and compile the taxonomy now.",
        model=config.MODEL_DECODE,
        server_tools=[_WEB_SEARCH_TOOL],
    )
    if result is None:
        logger.warning("taxonomy research failed")
        return False
    n_fear = store.add_concepts("fear", result.get("fears") or [], "web")
    n_why = store.add_concepts("why", result.get("whys") or [], "web")
    store.set_state("last_web_refresh_at", datetime.now(timezone.utc).isoformat())
    logger.info("taxonomy updated: +%d fears, +%d whys", n_fear, n_why)
    return True


def bootstrap() -> bool:
    return _run("")


def refresh() -> bool:
    fears = [c["text"] for c in store.active_concepts("fear")]
    whys = [c["text"] for c in store.active_concepts("why")]
    return _run(_REFRESH_EXTRA.format(fears="; ".join(fears) or "(none)",
                                      whys="; ".join(whys) or "(none)"))
