import os
import time
import io
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from database import db

MAX_SNIPES_PER_CHANNEL = 20
MAX_CACHED_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_CACHED_ATTACHMENT_BYTES = 32 * 1024 * 1024


class Snipe(commands.Cog):
    """,s to recover the last deleted message, ,rs for the last removed
    reaction, ,bc to bulk-delete only bot messages. All in-memory — history
    resets if the bot restarts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # channel_id -> deque of deleted message snapshots, newest first
        self.deleted_messages: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_SNIPES_PER_CHANNEL))
        # channel_id -> deque of removed reaction snapshots, newest first
        self.removed_reactions: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_SNIPES_PER_CHANNEL))

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None:
            return

        attachments = []

        # Cache the attachment bytes while Discord's attachment URL is still
        # usable. This is much more reliable than only saving a CDN URL:
        # deleted-message attachment URLs can eventually stop working.
        for attachment in message.attachments:
            item = {
                "url": attachment.url,
                "filename": attachment.filename,
                "content_type": attachment.content_type or "",
                "data": None,
            }

            # Only cache reasonably-sized files in RAM. Images are the main
            # target here; large videos/files remain represented by their URL.
            if attachment.size <= MAX_CACHED_ATTACHMENT_BYTES:
                try:
                    data = await attachment.read(use_cached=False)
                    if len(data) <= MAX_CACHED_ATTACHMENT_BYTES:
                        item["data"] = data
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    pass

            attachments.append(item)

        self.deleted_messages[message.channel.id].appendleft({
            "author": message.author,
            "content": message.content,
            "attachments": attachments,
            "deleted_at": time.time(),
        })

        # Keep memory bounded across all channels.
        total = 0
        for history in self.deleted_messages.values():
            for entry in history:
                for attachment in entry.get("attachments", []):
                    data = attachment.get("data")
                    if data:
                        total += len(data)

        if total > MAX_TOTAL_CACHED_ATTACHMENT_BYTES:
            for history in self.deleted_messages.values():
                for entry in reversed(history):
                    for attachment in entry.get("attachments", []):
                        if attachment.get("data"):
                            attachment["data"] = None
                            total -= 1
                            # We only need to aggressively trim until the
                            # cache is safely under the configured limit.
                            if total <= MAX_TOTAL_CACHED_ATTACHMENT_BYTES // 2:
                                break
                    if total <= MAX_TOTAL_CACHED_ATTACHMENT_BYTES // 2:
                        break
                if total <= MAX_TOTAL_CACHED_ATTACHMENT_BYTES // 2:
                    break

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        user = guild.get_member(payload.user_id) if guild else None
        self.removed_reactions[payload.channel_id].appendleft({
            "emoji": str(payload.emoji),
            "user": user or payload.user_id,
            "message_id": payload.message_id,
            "removed_at": time.time(),
        })

    @commands.command(name="s", description="Show the most recently deleted message in this channel.")
    @app_commands.describe(index="How far back to look (1 = most recent, default 1)")
    async def snipe(self, ctx: commands.Context, index: commands.Range[int, 1, MAX_SNIPES_PER_CHANNEL] = 1):
        history = self.deleted_messages.get(ctx.channel.id)
        if not history or index > len(history):
            return await ctx.send("Nothing to snipe here.")
        entry = history[index - 1]
        embed = discord.Embed(
            description=entry["content"] or "*(no text content)*",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow().fromtimestamp(entry["deleted_at"]),
        )
        embed.set_author(
            name=str(entry["author"]),
            icon_url=entry["author"].display_avatar.url
        )

        attachments = entry.get("attachments", [])

        # Discord embeds cannot directly display a Python bytes object.
        # Re-upload the cached image with the snipe message instead. This
        # means ,s still shows the actual image after the original message
        # has been deleted.
        files = []
        image_attachment = None

        for i, attachment in enumerate(attachments):
            data = attachment.get("data")
            content_type = (attachment.get("content_type") or "").lower()
            filename = attachment.get("filename") or f"attachment_{i}"

            if data and content_type.startswith("image/"):
                safe_name = filename.replace("/", "_").replace("\\", "_")
                files.append(
                    discord.File(
                        io.BytesIO(data),
                        filename=safe_name
                    )
                )
                if image_attachment is None:
                    image_attachment = safe_name

        if image_attachment:
            embed.set_image(url=f"attachment://{image_attachment}")
        elif attachments:
            # If the attachment could not be cached, keep the original URL
            # as a fallback. It may still be valid.
            first_url = attachments[0].get("url")
            if first_url:
                embed.set_image(url=first_url)

        if attachments:
            extra = len(attachments) - len(files)
            if extra > 0:
                embed.add_field(
                    name="Attachments",
                    value=f"{len(attachments)} attachment(s)",
                    inline=True
                )

        embed.set_footer(text=f"{index}/{len(history)} deleted messages")
        await ctx.send(embed=embed, files=files)

    @commands.command(name="rs", description="Show the most recently removed reaction in this channel.")
    @app_commands.describe(index="How far back to look (1 = most recent, default 1)")
    async def reaction_snipe(self, ctx: commands.Context, index: commands.Range[int, 1, MAX_SNIPES_PER_CHANNEL] = 1):
        history = self.removed_reactions.get(ctx.channel.id)
        if not history or index > len(history):
            return await ctx.send("Nothing to snipe here.")
        entry = history[index - 1]
        user = entry["user"]
        user_text = user.mention if isinstance(user, discord.Member) else f"`{user}`"
        embed = discord.Embed(
            description=f"{user_text} removed {entry['emoji']} from [this message](https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{entry['message_id']})",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow().fromtimestamp(entry["removed_at"]),
        )
        embed.set_footer(text=f"{index}/{len(history)} removed reactions")
        await ctx.send(embed=embed)

    @commands.command(name="cs", description="Clear this channel's snipe and reaction-snipe history.")
    @commands.has_permissions(manage_messages=True)
    async def clear_snipes(self, ctx: commands.Context):
        self.deleted_messages.pop(ctx.channel.id, None)
        self.removed_reactions.pop(ctx.channel.id, None)
        await ctx.send("🧹 Snipe history cleared for this channel.")

    @commands.hybrid_command(name="bc", description="Bulk delete recent bot messages and command messages in this channel.")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    @app_commands.describe(amount="How many recent messages to scan (default 50, max 200)")
    async def clear_bot_messages(self, ctx: commands.Context, amount: commands.Range[int, 1, 200] = 50):
        row = await db.get_guild_config(ctx.guild.id)
        default_prefix = os.getenv("DEFAULT_PREFIX", ",")
        prefixes = {row["prefix"], default_prefix}
        mention_prefixes = (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>")

        def is_bot_or_command(m: discord.Message) -> bool:
            if m.author.bot:
                return True
            if m.content.startswith(mention_prefixes):
                return True
            return any(m.content.startswith(p) for p in prefixes if p)

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
            deleted = await ctx.channel.purge(limit=amount, check=is_bot_or_command)
            await ctx.interaction.followup.send(f"🧹 Deleted {len(deleted)} message(s).", ephemeral=True)
        else:
            deleted = await ctx.channel.purge(limit=amount + 1, check=lambda m: is_bot_or_command(m) or m.id == ctx.message.id)
            msg = await ctx.send(f"🧹 Deleted {len(deleted) - 1} message(s).")
            await msg.delete(delay=4)


async def setup(bot: commands.Bot):
    await bot.add_cog(Snipe(bot))
