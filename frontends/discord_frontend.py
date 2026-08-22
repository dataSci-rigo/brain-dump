"""Discord frontend (discord.py). Owns the asyncio main loop; every pipeline
call goes through asyncio.to_thread so blocking Anthropic/sqlite work never
stalls the gateway heartbeat. Capture surface: DMs from the owner plus any
channels in BRAINDUMP_DISCORD_CHANNEL_IDS."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

import config
import db
import pipeline
from outbound import OutText

logger = logging.getLogger(__name__)

_CHUNK = 2000  # Discord message length cap


def _allowed(message: discord.Message) -> bool:
    if message.author.bot:
        return False
    if config.DISCORD_OWNER_ID and message.author.id != config.DISCORD_OWNER_ID:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    return message.channel.id in config.DISCORD_CHANNEL_IDS


class _ItemButtons(discord.ui.View):
    """Routing buttons for one actionable item. custom_id carries the same
    callback_data strings Telegram uses, so pipeline.handle_callback is shared."""

    def __init__(self, out: OutText):
        super().__init__(timeout=None)
        for row_i, row in enumerate(out.buttons):
            for btn in row:
                item = discord.ui.Button(label=btn.label, row=row_i,
                                         custom_id=btn.callback_data,
                                         style=discord.ButtonStyle.secondary)
                item.callback = self._make_callback(btn.callback_data)
                self.add_item(item)

    def _make_callback(self, data: str):
        async def _cb(interaction: discord.Interaction) -> None:
            ack, replacement = await asyncio.to_thread(pipeline.handle_callback, data)
            if replacement:
                await interaction.response.edit_message(content=replacement, view=None)
            else:
                await interaction.response.send_message(ack, ephemeral=True)
        return _cb


async def _deliver(channel: discord.abc.Messageable, outs: list[OutText],
                   reply_targets: dict[str, discord.Message] | None = None) -> None:
    for out in outs:
        view = _ItemButtons(out) if out.buttons else None
        reference = None
        if out.reply_to_id and reply_targets and out.reply_to_id in reply_targets:
            reference = reply_targets[out.reply_to_id]
        first_sent: discord.Message | None = None
        text = out.text
        while text:
            chunk, text = text[:_CHUNK], text[_CHUNK:]
            sent = await channel.send(
                chunk,
                view=view if not text and view else discord.utils.MISSING,
                reference=reference if first_sent is None else None,
            )
            if first_sent is None:
                first_sent = sent
        if first_sent and out.record_purpose and out.record_note_id:
            await asyncio.to_thread(
                db.record_bot_message, "discord", str(channel.id),
                str(first_sent.id), out.record_note_id, out.record_purpose)


class BrainDumpClient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        self.tree.add_command(app_commands.Command(
            name="search", description="Find notes and tasks",
            callback=_slash_search))
        self.tree.add_command(app_commands.Command(
            name="recent", description="List recent notes",
            callback=_slash_recent))
        await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info("Discord connected as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if not _allowed(message):
            return

        image_bytes = None
        mime = "image/jpeg"
        for att in message.attachments:
            if (att.content_type or "").startswith("image/"):
                image_bytes = await att.read()
                mime = att.content_type
                break

        inbound = pipeline.Inbound(
            platform="discord",
            chat_id=str(message.channel.id),
            user_id=str(message.author.id),
            message_id=str(message.id),
            text=message.content or None,
            image_bytes=image_bytes,
            image_mime=mime,
            reply_to_id=(str(message.reference.message_id)
                         if message.reference and message.reference.message_id else None),
        )
        outs = await asyncio.to_thread(pipeline.handle_message, inbound)
        await _deliver(message.channel, outs,
                       reply_targets={str(message.id): message})


async def _slash_search(interaction: discord.Interaction, query: str) -> None:
    await interaction.response.defer()
    outs = await asyncio.to_thread(pipeline.handle_search, query)
    text = "\n\n".join(o.text for o in outs) or "Nothing found."
    await interaction.followup.send(text[:_CHUNK])


async def _slash_recent(interaction: discord.Interaction, n: int = 10) -> None:
    await interaction.response.defer()
    outs = await asyncio.to_thread(
        pipeline.handle_message,
        pipeline.Inbound(platform="discord", chat_id=str(interaction.channel_id),
                         user_id=str(interaction.user.id), text=f"/recent {n}"))
    text = "\n\n".join(o.text for o in outs) or "No notes yet."
    await interaction.followup.send(text[:_CHUNK])


def run() -> None:
    BrainDumpClient().run(config.DISCORD_TOKEN, log_handler=None)
