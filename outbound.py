"""Platform-agnostic outbound message shapes. The pipeline returns these;
each frontend renders them natively (inline keyboards / discord Views)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Button:
    label: str
    callback_data: str  # <=64 bytes (Telegram callback_data limit)


@dataclass
class OutText:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    reply_to_id: str | None = None      # platform message id to reply to
    # when set, db.record_bot_message() is called with the sent message's id
    record_purpose: str | None = None   # 'confirmation' | 'questions'
    record_note_id: int | None = None
