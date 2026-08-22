"""Interpret a natural-language search query into FTS terms + filters."""
from __future__ import annotations

import config
from ai import client

_SCHEMA = {
    "type": "object",
    "properties": {
        "fts_query": {
            "type": "string",
            "description": ("SQLite FTS5 MATCH expression: OR-joined stems of the"
                            " content words, e.g. 'farmer OR market OR vegetable'."
                            " No column filters, no NEAR."),
        },
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "Tags from the known list that match the query intent."},
        "since": {"type": ["string", "null"],
                  "description": "ISO date lower bound if the query implies recency"
                                 " ('last week', 'yesterday'), else null."},
        "include_tasks": {"type": "boolean",
                          "description": "True unless the query is clearly notes-only."},
    },
    "required": ["fts_query", "tags", "include_tasks"],
}


def interpret_query(query: str, known_tags: list[str], today_iso: str) -> dict | None:
    return client.call_tool(
        system=("You turn a natural-language search over personal notes/tasks into"
                f" structured filters. Today is {today_iso}."
                f" Known tags: {', '.join(known_tags) or '(none)'}"),
        user_message=query,
        tool_name="build_search",
        tool_description="Build the structured search from the user's query.",
        input_schema=_SCHEMA,
        model=config.MODEL_FAST,
    )
