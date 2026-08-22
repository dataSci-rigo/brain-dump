"""Telegram frontend: synchronous requests long-polling (mirrors
semantic_task_manager/telegram_api.py — PTB async has repeatedly dropped
messages in this environment). Runs in a daemon thread under main.py; calls
the pipeline directly since everything here may block."""
from __future__ import annotations

import logging
import time

import requests

import config
import db
import pipeline
from outbound import OutText

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
_FILE_URL = f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}"


# ── thin API wrapper ─────────────────────────────────────────────────────────

def get_updates(offset: int, timeout: int = 30) -> list[dict]:
    try:
        resp = requests.get(
            f"{_BASE_URL}/getUpdates",
            params={"timeout": timeout, "offset": offset, "limit": 100,
                    "allowed_updates": '["message","callback_query"]'},
            timeout=timeout + 10,
        )
        return resp.json().get("result", [])
    except Exception as e:
        logger.warning("poll error: %s", e)
        return []


def send_message(text: str, reply_to: int | None = None,
                 reply_markup: dict | None = None) -> int | None:
    payload: dict = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4096]}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to,
                                       "allow_sending_without_reply": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{_BASE_URL}/sendMessage", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        logger.warning("sendMessage failed: %s", data)
    except Exception as e:
        logger.warning("send error: %s", e)
    return None


def edit_message(message_id: int, text: str) -> None:
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": message_id,
               "text": text[:4096]}
    try:
        resp = requests.post(f"{_BASE_URL}/editMessageText", json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok") and "message is not modified" not in data.get("description", ""):
            logger.warning("editMessageText failed: %s", data)
    except Exception as e:
        logger.warning("edit error: %s", e)


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    try:
        requests.post(f"{_BASE_URL}/answerCallbackQuery", json=payload, timeout=10)
    except Exception as e:
        logger.warning("answerCallbackQuery error: %s", e)


def set_my_commands(commands: list[tuple[str, str]]) -> None:
    payload = {"commands": [{"command": c, "description": d} for c, d in commands]}
    try:
        requests.post(f"{_BASE_URL}/setMyCommands", json=payload, timeout=10)
    except Exception as e:
        logger.warning("setMyCommands error: %s", e)


def download_file(file_id: str) -> bytes | None:
    try:
        resp = requests.get(f"{_BASE_URL}/getFile", params={"file_id": file_id},
                            timeout=10)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("getFile failed: %s", data)
            return None
        file_resp = requests.get(f"{_FILE_URL}/{data['result']['file_path']}",
                                 timeout=30)
        return file_resp.content
    except Exception as e:
        logger.warning("download_file error: %s", e)
        return None


# ── rendering ────────────────────────────────────────────────────────────────

def _keyboard(out: OutText) -> dict | None:
    if not out.buttons:
        return None
    return {"inline_keyboard": [
        [{"text": b.label, "callback_data": b.callback_data[:64]} for b in row]
        for row in out.buttons]}


def deliver(outs: list[OutText]) -> None:
    for out in outs:
        reply_to = int(out.reply_to_id) if out.reply_to_id else None
        msg_id = send_message(out.text, reply_to=reply_to, reply_markup=_keyboard(out))
        if msg_id and out.record_purpose and out.record_note_id:
            db.record_bot_message("telegram", str(config.TELEGRAM_CHAT_ID),
                                  str(msg_id), out.record_note_id, out.record_purpose)


# ── update processing ────────────────────────────────────────────────────────

def _process_message(msg: dict) -> None:
    if msg.get("chat", {}).get("id") != config.TELEGRAM_CHAT_ID:
        return  # single-owner bot

    image_bytes = None
    mime = "image/jpeg"
    if msg.get("photo"):
        image_bytes = download_file(msg["photo"][-1]["file_id"])
    elif msg.get("document", {}).get("mime_type", "").startswith("image/"):
        mime = msg["document"]["mime_type"]
        image_bytes = download_file(msg["document"]["file_id"])

    inbound = pipeline.Inbound(
        platform="telegram",
        chat_id=str(msg["chat"]["id"]),
        user_id=str(msg.get("from", {}).get("id", "")),
        message_id=str(msg["message_id"]),
        text=msg.get("text") or msg.get("caption"),
        image_bytes=image_bytes,
        image_mime=mime,
        reply_to_id=(str(msg["reply_to_message"]["message_id"])
                     if msg.get("reply_to_message") else None),
    )
    deliver(pipeline.handle_message(inbound))


def _process_callback(cq: dict) -> None:
    ack, replacement = pipeline.handle_callback(cq.get("data", ""))
    answer_callback_query(cq["id"], ack)
    if replacement and cq.get("message"):
        edit_message(cq["message"]["message_id"], replacement)


def run_polling() -> None:
    logger.info("Telegram poller starting (chat %s)", config.TELEGRAM_CHAT_ID)
    set_my_commands(pipeline.COMMANDS)
    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = max(offset, update["update_id"] + 1)
            try:
                if "message" in update:
                    _process_message(update["message"])
                elif "callback_query" in update:
                    _process_callback(update["callback_query"])
            except Exception:
                logger.exception("error processing update %s", update.get("update_id"))
        if not updates:
            time.sleep(1)
