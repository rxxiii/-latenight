import base64
import datetime
import json
import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from database import db

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Gemini's inline (base64-in-request) upload limit is ~20MB — stay under it.
MAX_FILE_BYTES = 19 * 1024 * 1024

CLASSIFY_PROMPT = (
    "You are a strict Discord media safety classifier. Analyze the attached image, "
    "GIF, or video. Flag clearly unsafe visual content. Be especially strict about "
    "nudity and sexual content: flag photographs, GIFs, drawings, anime, manga, "
    "cartoons, CGI, game footage, and AI-generated media showing exposed genitals, "
    "nipples/breasts, explicit sexual activity, or clearly sexualized nudity. "
    "Also flag graphic gore and blood, including realistic or fictional blood, "
    "graphic wounds, dismemberment, exposed organs, corpses, severe injuries, "
    "and graphic violence. Fictional, animated, illustrated, or game gore counts "
    "too. Flag graphic self-harm, extreme animal gore/cruelty, extreme violent "
    "death, and clearly violent extremist propaganda. Do not flag normal clothing, "
    "ordinary swimwear, non-sexual medical diagrams, harmless red objects/liquids, "
    "or non-graphic violence. "
    "For animated media, consider the entire supplied media and flag if prohibited "
    "content appears in any visible frame. "
    'Respond ONLY with compact JSON in this exact shape: '
    '{"flagged": true or false, "category": "gore" or "pornography" or "minor_sexual_content" or "violence" or "self_harm" or "animal_gore" or "extremism" or "none", "reason": "brief reason"}'
)


class ContentFilter(commands.Cog):
    """Scans uploaded images/videos with the Gemini API and removes anything
    flagged as gore, pornography, or a Discord ToS violation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    @commands.hybrid_group(name="contentfilter", aliases=["cf"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def contentfilter(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)
        embed = discord.Embed(title="Content Filter (Gemini)", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value="✅" if row["content_filter_enabled"] else "❌")
        embed.add_field(name="Action", value=row["content_filter_action"] or "delete")
        embed.add_field(
            name="Log channel",
            value=f"<#{row['content_filter_log_channel_id']}>" if row["content_filter_log_channel_id"] else "None",
        )
        if not GEMINI_API_KEY:
            embed.add_field(name="⚠️ Warning", value="No `GEMINI_API_KEY` set in the bot's environment — this won't work yet.", inline=False)
        await ctx.send(embed=embed)

    @contentfilter.command(name="enable")
    async def contentfilter_enable(self, ctx: commands.Context):
        await db.set_guild_config(ctx.guild.id, content_filter_enabled=1)
        await ctx.send("✅ Content filter enabled.")

    @contentfilter.command(name="disable")
    async def contentfilter_disable(self, ctx: commands.Context):
        await db.set_guild_config(ctx.guild.id, content_filter_enabled=0)
        await ctx.send("Content filter disabled.")

    @contentfilter.command(name="logs")
    @app_commands.describe(channel="Channel to log flagged content in")
    async def contentfilter_logs(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.set_guild_config(ctx.guild.id, content_filter_log_channel_id=channel.id)
        await ctx.send(f"Flagged content will be logged in {channel.mention}.")

    @contentfilter.command(name="action")
    @app_commands.describe(action="What to do when content is flagged: delete, warn, timeout, or ban")
    async def contentfilter_action(self, ctx: commands.Context, action: str):
        action = action.lower()
        if action not in ("delete", "warn", "timeout", "ban"):
            return await ctx.send("Action must be one of: delete, warn, timeout, ban")
        await db.set_guild_config(ctx.guild.id, content_filter_action=action)
        await ctx.send(f"Action on flagged content set to **{action}**.")

    async def _classify(self, url: str, mime_type: str):
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
        except Exception:
            return None

        if len(data) > MAX_FILE_BYTES:
            return None  # too large to scan inline — skipped, not flagged

        payload = {
            "contents": [{
                "parts": [
                    {"text": CLASSIFY_PROMPT},
                    {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode()}},
                ]
            }],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        try:
            async with self.session.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
        except Exception:
            return None

        try:
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError):
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not message.attachments or not GEMINI_API_KEY:
            return

        row = await db.get_guild_config(message.guild.id)
        if not row["content_filter_enabled"]:
            return

        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if not (content_type.startswith("image/") or content_type.startswith("video/")):
                continue

            result = await self._classify(attachment.url, content_type)
            if result and result.get("flagged"):
                await self._take_action(message, row, result)
                break

    async def _take_action(self, message: discord.Message, row, result: dict):
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        action = row["content_filter_action"] or "delete"
        member = message.author
        category = result.get("category", "unknown")
        reason = result.get("reason", "Flagged by content filter")

        if action == "warn" and isinstance(member, discord.Member):
            await db.add_warning(message.guild.id, member.id, self.bot.user.id, f"Content filter: {reason}")
        elif action == "timeout" and isinstance(member, discord.Member):
            try:
                await member.timeout(datetime.timedelta(hours=1), reason=f"Content filter: {reason}")
            except discord.HTTPException:
                pass
        elif action == "ban" and isinstance(member, discord.Member):
            try:
                await message.guild.ban(member, reason=f"Content filter: {reason}")
            except discord.HTTPException:
                pass

        if row["content_filter_log_channel_id"]:
            channel = message.guild.get_channel(row["content_filter_log_channel_id"])
            if channel:
                embed = discord.Embed(
                    title="🚫 Content Filter Triggered",
                    description=(
                        f"**User:** {member.mention}\n**Category:** {category}\n"
                        f"**Reason:** {reason}\n**Action taken:** {action}"
                    ),
                    color=discord.Color.red(),
                )
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ContentFilter(bot))
