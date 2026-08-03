import os
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from database import db

MAX_SNIPES_PER_CHANNEL = 20


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
        self.deleted_messages[message.channel.id].appendleft({
            "author": message.author,
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "deleted_at": time.time(),
        })

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

    @commands.hybrid_command(name="s", description="Show the most recently deleted message in this channel.")
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
        embed.set_author(name=str(entry["author"]), icon_url=entry["author"].display_avatar.url)
        if entry["attachments"]:
            embed.set_image(url=entry["attachments"][0])
        embed.set_footer(text=f"{index}/{len(history)} deleted messages")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="rs", description="Show the most recently removed reaction in this channel.")
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

    @commands.hybrid_command(name="cs", description="Clear this channel's snipe and reaction-snipe history.")
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
