import datetime
import re
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from database import db

INVITE_PATTERN = re.compile(r"(discord\.gg|discord(app)?\.com/invite)/\S+", re.IGNORECASE)

# Simple spam heuristic: if a member sends `SPAM_MESSAGE_COUNT` messages
# within `SPAM_WINDOW_SECONDS`, we treat it as spam.
SPAM_MESSAGE_COUNT = 5
SPAM_WINDOW_SECONDS = 5


class Filter(commands.Cog):
    """Word filter, invite link filter, and basic spam detection."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) -> deque of message timestamps
        self.message_times: dict[tuple[int, int], deque] = defaultdict(deque)

    @commands.hybrid_group(name="filter", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def filter_group(self, ctx: commands.Context):
        words = await db.get_filter_words(ctx.guild.id)
        config = await db.get_filter_config(ctx.guild.id)
        embed = discord.Embed(title="Filter Settings", color=discord.Color.blurple())
        embed.add_field(name="Blocked words", value=", ".join(f"`{w}`" for w in words) or "None", inline=False)
        embed.add_field(name="Invite filter", value="✅" if config["invites"] else "❌")
        embed.add_field(name="Spam filter", value="✅" if config["spam"] else "❌")
        await ctx.send(embed=embed)

    @filter_group.command(name="add")
    @app_commands.describe(word="Word or phrase to block")
    async def filter_add(self, ctx: commands.Context, *, word: str):
        await db.add_filter_word(ctx.guild.id, word)
        await ctx.send(f"Added `{word}` to the blocked word list.")

    @filter_group.command(name="remove")
    @app_commands.describe(word="Word or phrase to unblock")
    async def filter_remove(self, ctx: commands.Context, *, word: str):
        await db.remove_filter_word(ctx.guild.id, word)
        await ctx.send(f"Removed `{word}` from the blocked word list.")

    @filter_group.command(name="invites")
    @app_commands.describe(state="on or off")
    async def filter_invites(self, ctx: commands.Context, state: str):
        await db.set_filter_config(ctx.guild.id, invites=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Invite link filter: **{state}**")

    @filter_group.command(name="spam")
    @app_commands.describe(state="on or off")
    async def filter_spam(self, ctx: commands.Context, state: str):
        await db.set_filter_config(ctx.guild.id, spam=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Spam filter: **{state}**")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_messages:
            return  # don't filter staff

        config = await db.get_filter_config(message.guild.id)
        content = message.content.lower()

        # Blocked words
        words = await db.get_filter_words(message.guild.id)
        for word in words:
            if word in content:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                try:
                    await message.channel.send(
                        f"{message.author.mention} that message was removed (blocked word).",
                        delete_after=5,
                    )
                except discord.HTTPException:
                    pass
                return

        # Invite links
        if config["invites"] and INVITE_PATTERN.search(message.content):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            try:
                await message.channel.send(
                    f"{message.author.mention} invite links aren't allowed here.",
                    delete_after=5,
                )
            except discord.HTTPException:
                pass
            return

        # Spam detection
        if config["spam"]:
            key = (message.guild.id, message.author.id)
            now = time.time()
            times = self.message_times[key]
            times.append(now)
            while times and now - times[0] > SPAM_WINDOW_SECONDS:
                times.popleft()

            if len(times) >= SPAM_MESSAGE_COUNT:
                times.clear()
                try:
                    await message.channel.purge(
                        limit=SPAM_MESSAGE_COUNT + 1,
                        check=lambda m: m.author.id == message.author.id,
                    )
                except discord.HTTPException:
                    pass
                try:
                    await message.author.timeout(
                        datetime.timedelta(minutes=5),
                        reason="Antispam: message flood",
                    )
                except discord.HTTPException:
                    pass
                try:
                    await message.channel.send(
                        f"{message.author.mention} was muted for 5 minutes for spamming.",
                        delete_after=8,
                    )
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Filter(bot))
