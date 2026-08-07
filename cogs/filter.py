import datetime
import re
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from database import db

# Matches discord.gg, discord.com/invite, discordapp.com/invite, and the
# dsc.gg shortlink domain. We strip ALL whitespace from the message before
# testing against this, so "discord . gg / abc" still gets caught.
INVITE_PATTERN = re.compile(
    r"(discord\.gg|discord(?:app)?\.com/invite|dsc\.gg)/?[a-zA-Z0-9\-]+",
    re.IGNORECASE,
)

LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
    "@": "a", "$": "s", "!": "i",
})
NON_LETTER = re.compile(r"[^a-z]")
REPEATED_LETTER = re.compile(r"(.)\1{2,}")
WHITESPACE = re.compile(r"\s+")


def normalize_for_filter(text: str) -> str:
    """Collapses common bypass tricks so 'n i g g e r', 'n1gger', and
    'niggerrrr' all normalize to the same thing as the plain word."""
    text = text.lower().translate(LEET_MAP)
    text = NON_LETTER.sub("", text)  # drop spaces, punctuation, symbols used as separators
    text = REPEATED_LETTER.sub(r"\1\1", text)  # "niggerrrr" -> "nigger", "book" stays "book"
    return text


def normalize_for_duplicate_check(text: str) -> str:
    """Looser normalization for spam duplicate detection — just case and
    whitespace, so 'hey check this out' and 'HEY  check   this out' count
    as the same spammed message."""
    return WHITESPACE.sub(" ", text.strip().lower())


# ---------- tuning ----------
SPAM_MESSAGE_COUNT = 5          # N messages...
SPAM_WINDOW_SECONDS = 5         # ...within T seconds = flood spam
DUPLICATE_MESSAGE_COUNT = 3     # the same message repeated N times...
DUPLICATE_WINDOW_SECONDS = 20   # ...within T seconds = duplicate spam
MASS_MENTION_THRESHOLD = 5      # mentions in a single message = mention spam
SPAM_TIMEOUT_MINUTES = 5
INVITE_TIMEOUT_MINUTES = 5
WORD_TIMEOUT_MINUTES = 5


class Filter(commands.Cog):
    """Word filter, invite link filter, and multi-layer spam detection.
    Checks new messages, edited messages, and supports a manual backfill
    scan (,filter scan) over recent channel history."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) -> deque of message timestamps, for flood detection
        self.message_times: dict[tuple[int, int], deque] = defaultdict(deque)
        # (guild_id, user_id) -> deque of (timestamp, normalized_content), for duplicate detection
        self.recent_contents: dict[tuple[int, int], deque] = defaultdict(deque)

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
    @app_commands.describe(words="Word(s) to block — separate multiple with commas or new lines")
    async def filter_add(self, ctx: commands.Context, *, words: str):
        word_list = [w.strip() for w in re.split(r"[,\n]", words) if w.strip()]
        if not word_list:
            return await ctx.send("No valid words provided.")
        for word in word_list:
            await db.add_filter_word(ctx.guild.id, word)
        if len(word_list) == 1:
            await ctx.send(f"Added `{word_list[0]}` to the blocked word list.")
        else:
            await ctx.send(f"Added {len(word_list)} words to the blocked word list.")

    @filter_group.command(name="remove")
    @app_commands.describe(words="Word(s) to unblock — separate multiple with commas or new lines")
    async def filter_remove(self, ctx: commands.Context, *, words: str):
        word_list = [w.strip() for w in re.split(r"[,\n]", words) if w.strip()]
        if not word_list:
            return await ctx.send("No valid words provided.")
        for word in word_list:
            await db.remove_filter_word(ctx.guild.id, word)
        if len(word_list) == 1:
            await ctx.send(f"Removed `{word_list[0]}` from the blocked word list.")
        else:
            await ctx.send(f"Removed {len(word_list)} words from the blocked word list.")

    @filter_group.command(name="invites")
    @app_commands.describe(state="on or off")
    async def filter_invites(self, ctx: commands.Context, state: str):
        await db.set_filter_config(ctx.guild.id, invites=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Invite link filter: **{state}** (offenders are timed out for {INVITE_TIMEOUT_MINUTES} minutes)")

    @filter_group.command(name="spam")
    @app_commands.describe(state="on or off")
    async def filter_spam(self, ctx: commands.Context, state: str):
        await db.set_filter_config(ctx.guild.id, spam=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Spam filter: **{state}**")

    @filter_group.command(name="scan", description="Re-check recent messages in this channel against the current filter.")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, moderate_members=True)
    @app_commands.describe(amount="How many recent messages to scan (default 200, max 1000)")
    async def filter_scan(self, ctx: commands.Context, amount: commands.Range[int, 1, 1000] = 200):
        status = await ctx.send(f"🔍 Scanning the last {amount} messages in this channel...")
        caught = 0
        async for message in ctx.channel.history(limit=amount, before=status):
            try:
                actioned = await self._check_message(message)
                if actioned:
                    caught += 1
            except discord.HTTPException:
                continue
        await status.edit(content=f"✅ Scan complete — {caught} message(s) removed and their authors timed out.")

    async def _punish(self, message: discord.Message, reason: str, timeout_minutes: int, purge_check=None):
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        if purge_check:
            try:
                await message.channel.purge(limit=15, check=purge_check)
            except discord.HTTPException:
                pass
        if isinstance(message.author, discord.Member):
            try:
                await message.author.timeout(
                    datetime.timedelta(minutes=timeout_minutes), reason=f"Filter: {reason}"
                )
            except discord.HTTPException:
                pass
        try:
            await message.channel.send(
                f"{message.author.mention} was timed out for {timeout_minutes} minutes — {reason}.",
                delete_after=8,
            )
        except discord.HTTPException:
            pass
        key = (message.guild.id, message.author.id)
        self.message_times.pop(key, None)
        self.recent_contents.pop(key, None)

    async def _check_message(self, message: discord.Message) -> bool:
        """Runs the word-filter and invite-filter checks on a message
        (used for new messages, edited messages, and ,filter scan).
        Returns True if the message was actioned."""
        if message.author.bot or message.guild is None:
            return False
        if isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_messages:
            return False  # don't filter staff
        if not message.content:
            return False

        config = await db.get_filter_config(message.guild.id)
        content = message.content.lower()

        words = await db.get_filter_words(message.guild.id)
        normalized_content = normalize_for_filter(message.content)
        for word in words:
            normalized_word = normalize_for_filter(word)
            if word in content or (normalized_word and normalized_word in normalized_content):
                await self._punish(message, "a blocked word", WORD_TIMEOUT_MINUTES)
                return True

        if config["invites"]:
            stripped = WHITESPACE.sub("", message.content)
            if INVITE_PATTERN.search(stripped):
                await self._punish(message, "posting an invite link", INVITE_TIMEOUT_MINUTES)
                return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        actioned = await self._check_message(message)
        if actioned:
            return

        # Spam detection — three independent layers, live-tracking only
        # (doesn't apply to ,filter scan, which works on message content alone).
        config = await db.get_filter_config(message.guild.id)
        if config["spam"] and not (
            isinstance(message.author, discord.Member) and message.author.guild_permissions.manage_messages
        ):
            key = (message.guild.id, message.author.id)
            now = time.time()

            # Layer 1: mass mentions in a single message
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count >= MASS_MENTION_THRESHOLD:
                await self._punish(
                    message, "mass mentions", SPAM_TIMEOUT_MINUTES,
                    purge_check=lambda m: m.author.id == message.author.id,
                )
                return

            # Layer 2: message flood (N messages in T seconds)
            times = self.message_times[key]
            times.append(now)
            while times and now - times[0] > SPAM_WINDOW_SECONDS:
                times.popleft()
            if len(times) >= SPAM_MESSAGE_COUNT:
                await self._punish(
                    message, "sending messages too quickly", SPAM_TIMEOUT_MINUTES,
                    purge_check=lambda m: m.author.id == message.author.id,
                )
                return

            # Layer 3: the same message repeated several times, even if
            # spaced out enough to dodge the flood check above.
            if message.content.strip():
                normalized = normalize_for_duplicate_check(message.content)
                recents = self.recent_contents[key]
                recents.append((now, normalized))
                while recents and now - recents[0][0] > DUPLICATE_WINDOW_SECONDS:
                    recents.popleft()
                matching = sum(1 for _, c in recents if c == normalized)
                if matching >= DUPLICATE_MESSAGE_COUNT:
                    await self._punish(
                        message, "repeating the same message", SPAM_TIMEOUT_MINUTES,
                        purge_check=lambda m: m.author.id == message.author.id and normalize_for_duplicate_check(m.content) == normalized,
                    )
                    return

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.guild is None or before.content == after.content:
            return
        await self._check_message(after)


async def setup(bot: commands.Bot):
    await bot.add_cog(Filter(bot))
