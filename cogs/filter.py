import datetime
import re
import time
import unicodedata
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

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

# Common cross-alphabet lookalikes ("homoglyphs") that render nearly
# identical to Latin letters but are different characters, so plain
# lowercasing doesn't catch them — mainly Cyrillic and a few Greek letters.
HOMOGLYPH_MAP = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "і": "i", "ѕ": "s", "ԁ": "d", "ո": "n", "ϲ": "c", "ⅰ": "i", "ⅼ": "l",
    "α": "a", "ο": "o", "ρ": "p", "ѵ": "v", "ᴀ": "a", "ʙ": "b",
})

NON_LETTER = re.compile(r"[^a-z]")
NON_LETTER_KEEP_SPACE_STAR = re.compile(r"[^a-z\s*]")
NON_LETTER_KEEP_SPACE = re.compile(r"[^a-z\s]")
ZERO_WIDTH = re.compile(r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\u2800\u3000\ufeff]")
REPEATED_LETTER = re.compile(r"(.)\1{2,}")
WHITESPACE = re.compile(r"\s+")

# Characters commonly used to visually separate letters without adding a
# normal space. They are removed before matching so e.g. f\u200b.u\200b.c\u200b.k
# is treated like "fuck".
INVISIBLE_CHARS = set("\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b\u180c\u180d\u180e\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\u2061\u2062\u2063\u2064\u2066\u2067\u2068\u2069\u206a\u206b\u206c\u206d\u206e\u206f\u2800\ufeff")

# How many extra alphabetic characters a filtered word may contain while
# still being considered an obfuscated form. This catches forms such as
# "badxx", "xxbad", and "baxd" without treating every long word containing
# a short blocked word as a match.
MAX_INSERTED_LETTERS = 2


def _clean(text: str, keep_stars: bool = False) -> str:
    """Aggressive Unicode/obfuscation normalization used by the filter."""
    text = ZERO_WIDTH.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().translate(HOMOGLYPH_MAP).translate(LEET_MAP)
    text = (NON_LETTER_KEEP_SPACE_STAR if keep_stars else NON_LETTER_KEEP_SPACE).sub("", text)
    text = REPEATED_LETTER.sub(r"\1", text)
    return WHITESPACE.sub(" ", text).strip()


def _compact(text: str) -> str:
    """Return only normalized ASCII letters."""
    return NON_LETTER.sub("", _clean(text)).lower()


def _is_subsequence(needle: str, haystack: str) -> bool:
    """Whether needle appears in order inside haystack."""
    if not needle:
        return False
    i = 0
    for ch in haystack:
        if i < len(needle) and ch == needle[i]:
            i += 1
            if i == len(needle):
                return True
    return False


def _within_insert_tolerance(token: str, word: str) -> bool:
    """Catch a filtered word with a small number of inserted letters.

    This is intentionally length-bounded to reduce false positives. It does
    not use a broad fuzzy ratio because that would make short words match
    unrelated normal words too easily.
    """
    if not token or not word:
        return False
    if len(token) <= len(word) or len(token) > len(word) + MAX_INSERTED_LETTERS:
        return False
    return _is_subsequence(word, token)


def _token_variants(text: str) -> list[str]:
    """Build normalized token forms, including punctuation/space-obfuscated runs."""
    clean = _clean(text, keep_stars=True)
    variants = []
    for token in clean.split():
        compact = _compact(token.replace("*", ""))
        if compact:
            variants.append(compact)

    # Joining short tokens catches "b a d" and "b.a.d" style spacing.
    parts = [p for p in clean.split() if p]
    run = ""
    for part in parts:
        c = _compact(part.replace("*", ""))
        if len(c) == 1:
            run += c
        else:
            if run:
                variants.append(run)
            run = ""
    if run:
        variants.append(run)
    return variants


def _wildcard_match(token: str, word: str) -> bool:
    """Match '*' as an arbitrary single-character placeholder."""
    token = _compact(token.replace("*", "")) if "*" not in token else token
    if len(token) != len(word) or "*" not in token:
        return False
    return all(tc == "*" or tc == wc for tc, wc in zip(token, word))


def _punctuation_insensitive_forms(content: str) -> list[str]:
    """Return compact chunks formed by removing separators/punctuation."""
    normalized = _clean(content, keep_stars=True)
    # Keep each whitespace-separated token and also the entire message with
    # separators removed. The latter catches f.u.c.k and f-u-c-k.
    forms = [normalized]
    forms.extend(normalized.split())
    forms.append(_compact(normalized))
    return [f for f in forms if f]


def find_filter_match(content: str, filter_words: list[str]) -> str | None:
    """Aggressive blocked-word matching with layered anti-bypass checks.

    Handles case changes, Unicode normalization, accents, homoglyphs,
    leetspeak, zero-width characters, punctuation, whitespace/letter spacing,
    repeated letters, '*' obfuscation, and up to MAX_INSERTED_LETTERS extra
    letters around/inside a blocked term when the resulting token is only a
    little longer than the blocked term.

    No text filter can literally guarantee detection of every bypass. This
    deliberately favors catching common obfuscations while keeping length
    limits so short words do not trigger on ordinary long words.
    """
    if not content or not filter_words:
        return None

    text = _clean(content, keep_stars=True)
    compact_message = _compact(content)
    tokens = _token_variants(content)
    forms = _punctuation_insensitive_forms(content)

    for raw_word in filter_words:
        word = _clean(raw_word)
        if not word:
            continue

        compact_word = _compact(word)
        if not compact_word:
            continue

        # 1) Normal word/phrase matching after normalization.
        # Remove stars here because asterisks are often inserted as separators.
        text_no_star = text.replace("*", "")
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text_no_star):
            return raw_word

        # 2) Punctuation / whitespace / zero-width bypasses.
        if compact_word in compact_message:
            return raw_word

        # 3) '*' wildcard forms, including asterisks between letters.
        for token in text.split():
            if "*" in token and _wildcard_match(token, compact_word):
                return raw_word

        # 4) Letter-by-letter spacing and punctuation splitting.
        for form in forms:
            if compact_word in _compact(form):
                return raw_word

        # 5) Small insertion tolerance. Catches intentional extra letters
        # such as "wordx", "xword", and "woXrd" while remaining bounded.
        # For very short terms, only one inserted character is allowed.
        allowed = 1 if len(compact_word) <= 3 else MAX_INSERTED_LETTERS
        for token in tokens:
            if len(token) <= len(compact_word) + allowed and len(token) > len(compact_word):
                if _is_subsequence(compact_word, token):
                    return raw_word

        # 6) Consecutive repeated-character bypass after normalization.
        squashed = REPEATED_LETTER.sub(r"\1", compact_message)
        if compact_word in squashed:
            return raw_word

    return None


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
SPAM_WARNING_REASON = "Spam (5 messages within 5 seconds)"
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
        self.auto_scan.start()

    def cog_unload(self):
        self.auto_scan.cancel()

    @commands.hybrid_group(name="filter", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def filter_group(self, ctx: commands.Context):
        await ctx.invoke(self.filter_list)

    @filter_group.command(name="list", description="Show the global blocked word list (shared across every server).")
    @commands.has_permissions(manage_guild=True)
    async def filter_list(self, ctx: commands.Context):
        words = await db.get_global_filter_words()
        config = await db.get_filter_config(ctx.guild.id)
        embed = discord.Embed(title="Filter Settings", color=discord.Color.blurple())
        embed.add_field(name=f"Blocked words ({len(words)}) — global, shared across all servers", value=", ".join(f"`{w}`" for w in words)[:1024] or "None", inline=False)
        embed.add_field(name="Invite filter (this server)", value="✅" if config["invites"] else "❌")
        embed.add_field(name="Spam filter (this server)", value="✅" if config["spam"] else "❌")
        await ctx.send(embed=embed)

    @filter_group.command(name="add", description="Add word(s) to the GLOBAL blocked list — applies to every server this bot is in.")
    @commands.is_owner()
    @app_commands.describe(words="Word(s) to block — separate multiple with commas or new lines")
    async def filter_add(self, ctx: commands.Context, *, words: str):
        word_list = [w.strip() for w in re.split(r"[,\n]", words) if w.strip()]
        if not word_list:
            return await ctx.send("No valid words provided.")
        for word in word_list:
            await db.add_global_filter_word(word)
        if len(word_list) == 1:
            await ctx.send(f"Added `{word_list[0]}` to the global blocked word list (applies everywhere).")
        else:
            await ctx.send(f"Added {len(word_list)} words to the global blocked word list (applies everywhere).")

    @filter_group.command(name="remove", description="Remove word(s) from the GLOBAL blocked list.")
    @commands.is_owner()
    @app_commands.describe(words="Word(s) to unblock — separate multiple with commas or new lines")
    async def filter_remove(self, ctx: commands.Context, *, words: str):
        word_list = [w.strip() for w in re.split(r"[,\n]", words) if w.strip()]
        if not word_list:
            return await ctx.send("No valid words provided.")
        for word in word_list:
            await db.remove_global_filter_word(word)
        if len(word_list) == 1:
            await ctx.send(f"Removed `{word_list[0]}` from the global blocked word list.")
        else:
            await ctx.send(f"Removed {len(word_list)} words from the global blocked word list.")

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

    @tasks.loop(minutes=15)
    async def auto_scan(self):
        global_words = await db.get_global_filter_words()
        for guild in self.bot.guilds:
            config = await db.get_filter_config(guild.id)
            if not global_words and not config["invites"]:
                continue  # nothing this guild wants auto-checked

            for channel in guild.text_channels:
                try:
                    async for message in channel.history(limit=50):
                        try:
                            await self._check_message(message)
                        except discord.HTTPException:
                            continue
                except (discord.Forbidden, discord.HTTPException):
                    continue

    @auto_scan.before_loop
    async def before_auto_scan(self):
        await self.bot.wait_until_ready()

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

    async def _punish_spam(self, message: discord.Message, reason: str) -> None:
        """Handle spam with deletion + a warning, without timing out the user.

        The spam filter intentionally does not purge or timeout. It removes the
        triggering message, records a warning in the normal warning system, and
        sends a short notice in the channel.
        """
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        if isinstance(message.author, discord.Member):
            try:
                await db.add_warning(
                    message.guild.id,
                    message.author.id,
                    self.bot.user.id if self.bot.user else 0,
                    reason,
                )
            except Exception:
                # The moderation action should still complete if the warning
                # database write has a transient problem.
                pass

        try:
            await message.channel.send(
                f"⚠️ {message.author.mention} warned for spam — {reason}.",
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

        words = await db.get_global_filter_words()
        matched = find_filter_match(message.content, words) if words else None
        if matched:
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
                await self._punish_spam(message, "mass mentions")
                return

            # Layer 2: message flood (N messages in T seconds)
            times = self.message_times[key]
            times.append(now)
            while times and now - times[0] > SPAM_WINDOW_SECONDS:
                times.popleft()
            if len(times) >= SPAM_MESSAGE_COUNT:
                await self._punish_spam(message, SPAM_WARNING_REASON)
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
                    await self._punish_spam(message, "repeating the same message")
                    return

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.guild is None or before.content == after.content:
            return
        await self._check_message(after)


async def setup(bot: commands.Bot):
    await bot.add_cog(Filter(bot))
