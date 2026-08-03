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
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# Keep comfortably below Gemini's inline-data request limit.
MAX_FILE_BYTES = int(
    os.getenv("CONTENT_FILTER_MAX_FILE_MB", "19")
) * 1024 * 1024

MAX_ATTACHMENTS_PER_MESSAGE = 10


CLASSIFY_PROMPT = """
You are a STRICT Discord media safety classifier.

Analyze the attached IMAGE OR VIDEO and determine whether it should be
removed from a general-audience Discord server.

IMPORTANT: Be aggressive about BLOOD and GORE.

FLAG "gore" when ANY of the following are clearly present:

- Graphic blood or bleeding
- Large amounts of blood
- Blood covering a person, animal, object, or scene
- Graphic wounds
- Open wounds
- Exposed flesh
- Exposed organs
- Dismemberment
- Decapitation
- Corpses shown graphically
- Severely mutilated bodies
- Graphic medical trauma
- Graphic injury close-ups
- Graphic death
- Extreme violence
- Gore compilations

This applies EVEN IF THE CONTENT IS:
- A drawing
- Anime
- Manga
- Cartoon
- Illustration
- Digital art
- CGI
- A video game
- A meme
- AI-generated
- Fictional

Therefore, graphic fictional/cartoon/anime/game blood and gore MUST also
be flagged.

Do NOT flag something merely because it contains the color red, a tiny
non-graphic injury, ketchup, tomato sauce, or ordinary non-graphic violence.

FLAG "pornography" when there is:
- Explicit sexual activity
- Sexual intercourse
- Explicit sexual acts
- Visible genitals in a sexual context
- Explicit nudity intended to be sexual
- Sexual fluids
- Clearly pornographic material
- Explicit sexual drawings/anime/manga
- Explicit digitally generated sexual imagery

FLAG "minor_sexual_content" for sexual or exploitative content involving
someone who appears to be under 18. Treat this category extremely
seriously.

FLAG "violence" for extreme real-world violence or graphic violent death,
even when the image does not clearly fit the gore category.

FLAG "self_harm" for graphic depictions of suicide, suicide attempts,
self-harm injuries, or graphic self-harm methods.

FLAG "animal_gore" for graphic animal injuries, mutilation, slaughter,
animal corpses, or extreme animal cruelty.

FLAG "extremism" for clearly identifiable extremist/terrorist propaganda
that promotes or glorifies serious violence.

For all other content, only flag it when it is clearly unsafe or clearly
falls into one of the categories above.

Do NOT flag:
- Normal clothing
- Normal swimwear
- Non-sexual medical diagrams
- Ordinary non-graphic injuries
- Harmless red objects/liquids
- Normal fictional fighting without graphic injury
- Non-explicit romance
- Mild cartoon violence

Return ONLY valid compact JSON.

Use exactly this structure:

{
  "flagged": true,
  "category": "gore",
  "reason": "brief explanation"
}

Allowed categories:
"gore"
"pornography"
"minor_sexual_content"
"violence"
"self_harm"
"animal_gore"
"extremism"
"other_violation"
"none"

If nothing should be removed, return:

{
  "flagged": false,
  "category": "none",
  "reason": "No prohibited content detected."
}
"""


class ContentFilter(commands.Cog):
    """Scans uploaded images/videos with Gemini and removes flagged media."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    @commands.hybrid_group(
        name="contentfilter",
        aliases=["cf"],
        invoke_without_command=True
    )
    @commands.has_permissions(manage_guild=True)
    async def contentfilter(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)

        embed = discord.Embed(
            title="Content Filter (Gemini)",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Enabled",
            value="✅" if row["content_filter_enabled"] else "❌"
        )

        embed.add_field(
            name="Action",
            value=row["content_filter_action"] or "delete"
        )

        embed.add_field(
            name="Log channel",
            value=(
                f"<#{row['content_filter_log_channel_id']}>"
                if row["content_filter_log_channel_id"]
                else "None"
            )
        )

        if not GEMINI_API_KEY:
            embed.add_field(
                name="⚠️ Warning",
                value=(
                    "No `GEMINI_API_KEY` is configured. "
                    "Media scanning will not work."
                ),
                inline=False
            )

        await ctx.send(embed=embed)

    @contentfilter.command(name="enable")
    async def contentfilter_enable(self, ctx: commands.Context):
        await db.set_guild_config(
            ctx.guild.id,
            content_filter_enabled=1
        )
        await ctx.send("✅ Content filter enabled.")

    @contentfilter.command(name="disable")
    async def contentfilter_disable(self, ctx: commands.Context):
        await db.set_guild_config(
            ctx.guild.id,
            content_filter_enabled=0
        )
        await ctx.send("Content filter disabled.")

    @contentfilter.command(name="logs")
    @app_commands.describe(
        channel="Channel to log flagged content in"
    )
    async def contentfilter_logs(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel
    ):
        await db.set_guild_config(
            ctx.guild.id,
            content_filter_log_channel_id=channel.id
        )

        await ctx.send(
            f"Flagged content will be logged in {channel.mention}."
        )

    @contentfilter.command(name="action")
    @app_commands.describe(
        action="delete, warn, timeout, or ban"
    )
    async def contentfilter_action(
        self,
        ctx: commands.Context,
        action: str
    ):
        action = action.lower()

        if action not in (
            "delete",
            "warn",
            "timeout",
            "ban"
        ):
            return await ctx.send(
                "Action must be one of: "
                "`delete`, `warn`, `timeout`, `ban`"
            )

        await db.set_guild_config(
            ctx.guild.id,
            content_filter_action=action
        )

        await ctx.send(
            f"Action on flagged content set to **{action}**."
        )

    async def _classify(
        self,
        url: str,
        mime_type: str
    ):
        if not self.session:
            return None

        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:

                if resp.status != 200:
                    return None

                data = await resp.read()

        except Exception:
            return None

        # Don't pretend oversized files were scanned.
        if len(data) > MAX_FILE_BYTES:
            return {
                "flagged": False,
                "category": "unscanned",
                "reason": "File is larger than the configured scan limit."
            }

        encoded = base64.b64encode(data).decode("ascii")

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": CLASSIFY_PROMPT
                        },
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": encoded
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0
            }
        }

        try:
            async with self.session.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:

                if resp.status != 200:
                    return None

                result = await resp.json()

        except Exception:
            return None

        try:
            response_text = (
                result["candidates"][0]
                ["content"]["parts"][0]
                ["text"]
            )

            parsed = json.loads(response_text)

            # Normalize the result.
            return {
                "flagged": bool(parsed.get("flagged", False)),
                "category": str(
                    parsed.get("category", "none")
                ),
                "reason": str(
                    parsed.get(
                        "reason",
                        "Flagged by content filter"
                    )
                )
            }

        except (
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError
        ):
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        if (
            message.author.bot
            or message.guild is None
            or not message.attachments
            or not GEMINI_API_KEY
        ):
            return

        row = await db.get_guild_config(message.guild.id)

        if not row["content_filter_enabled"]:
            return

        attachments = message.attachments[
            :MAX_ATTACHMENTS_PER_MESSAGE
        ]

        for attachment in attachments:

            content_type = (
                attachment.content_type or ""
            ).lower()

            # Discord doesn't always provide a content type,
            # so fall back to the filename.
            if not content_type:
                filename = attachment.filename.lower()

                if filename.endswith(
                    (".jpg", ".jpeg", ".png", ".webp", ".gif")
                ):
                    content_type = "image/jpeg"

                elif filename.endswith(
                    (
                        ".mp4",
                        ".mov",
                        ".webm",
                        ".m4v",
                        ".mpeg",
                        ".mpg"
                    )
                ):
                    content_type = "video/mp4"

            if not (
                content_type.startswith("image/")
                or content_type.startswith("video/")
            ):
                continue

            result = await self._classify(
                attachment.url,
                content_type
            )

            if not result:
                continue

            if result.get("flagged"):

                await self._take_action(
                    message,
                    row,
                    result
                )

                break

    async def _take_action(
        self,
        message: discord.Message,
        row,
        result: dict
    ):

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        action = (
            row["content_filter_action"]
            or "delete"
        )

        member = message.author

        category = result.get(
            "category",
            "unknown"
        )

        reason = result.get(
            "reason",
            "Flagged by content filter"
        )

        # Warn
        if (
            action == "warn"
            and isinstance(member, discord.Member)
        ):
            await db.add_warning(
                message.guild.id,
                member.id,
                self.bot.user.id,
                f"Content filter: {reason}"
            )

        # Timeout
        elif (
            action == "timeout"
            and isinstance(member, discord.Member)
        ):
            try:
                await member.timeout(
                    datetime.timedelta(hours=1),
                    reason=f"Content filter: {reason}"
                )
            except discord.HTTPException:
                pass

        # Ban
        elif (
            action == "ban"
            and isinstance(member, discord.Member)
        ):
            try:
                await message.guild.ban(
                    member,
                    reason=f"Content filter: {reason}"
                )
            except discord.HTTPException:
                pass

        # Logging
        log_channel_id = row[
            "content_filter_log_channel_id"
        ]

        if log_channel_id:

            channel = message.guild.get_channel(
                log_channel_id
            )

            if channel:

                embed = discord.Embed(
                    title="🚫 Content Filter Triggered",
                    description=(
                        f"**User:** {member.mention}\n"
                        f"**Category:** `{category}`\n"
                        f"**Reason:** {reason}\n"
                        f"**Action:** `{action}`"
                    ),
                    color=discord.Color.red()
                )

                try:
                    await channel.send(
                        embed=embed
                    )
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ContentFilter(bot))
