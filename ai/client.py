"""Anthropic client helpers. Sync (the pipeline is synchronous — Discord
bridges via to_thread). call_json handles server tools (web_search) with the
pause_turn loop; call_tool forces a single tool for structured output
(hab7bot backend/app/ai/client.py pattern). Both return None on failure so
callers degrade gracefully — a dump is already saved before any AI runs."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic

import config

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _extract_json(raw: str) -> dict | None:
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # last resort: grab the outermost {...}
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    logger.warning("could not parse JSON from model output: %.200s", raw)
    return None


def call_json(*, system: str, content: list | str, model: str,
              server_tools: list[dict] | None = None, max_tokens: int = 16000,
              max_retries: int = 1, timeout: float = 300.0) -> tuple[dict | None, bool]:
    """One user turn -> fenced-JSON reply. Supports server tools (web_search):
    pause_turn re-calls with the partial content appended (max 3 continues).
    Returns (parsed_json | None, web_search_used)."""
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set; skipping AI call")
        return None, False

    client = _get_client()
    messages: list[dict] = [{"role": "user", "content": content}]
    for attempt in range(1, max_retries + 2):
        try:
            searched = False
            for _ in range(4):  # initial call + up to 3 pause_turn continues
                response = client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=messages, tools=server_tools or [], timeout=timeout,
                )
                searched = searched or any(
                    getattr(b, "type", "") == "server_tool_use" for b in response.content)
                if response.stop_reason == "pause_turn":
                    messages = messages + [{"role": "assistant", "content": response.content}]
                    continue
                break
            text = "".join(b.text for b in response.content if b.type == "text")
            return _extract_json(text), searched
        except Exception:
            logger.warning("AI call failed (attempt %d/%d)", attempt, max_retries + 1,
                           exc_info=True)
            messages = [{"role": "user", "content": content}]
    return None, False


def call_tool(*, system: str, user_message: str, tool_name: str,
              tool_description: str, input_schema: dict[str, Any], model: str,
              max_retries: int = 1, timeout: float = 30.0) -> dict[str, Any] | None:
    """Force a single tool call and return its parsed input. None on failure."""
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set; skipping AI call for %s", tool_name)
        return None

    client = _get_client()
    for attempt in range(1, max_retries + 2):
        try:
            response = client.messages.create(
                model=model, max_tokens=4096, system=system,
                messages=[{"role": "user", "content": user_message}],
                tools=[{"name": tool_name, "description": tool_description,
                        "input_schema": input_schema}],
                tool_choice={"type": "tool", "name": tool_name},
                timeout=timeout,
            )
        except Exception:
            logger.warning("AI call failed (attempt %d/%d) for tool %s",
                           attempt, max_retries + 1, tool_name, exc_info=True)
            continue
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input
        logger.warning("AI response for tool %s had no tool_use block", tool_name)
        return None
    return None
