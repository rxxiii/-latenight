import base64
import datetime
import io
import json
import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

from database import db


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

MAX_FILE_BYTES = int(
    os.getenv("CONTENT_FILTER_MAX_FILE_MB", "19")
) * 1024 * 1024

MAX_GIF_FRAMES = 15
MAX_ATTACHMENTS_PER_MESSAGE = 10


CLASSIFY_PROMPT = """
You are a STRICT Discord media safety classifier.

Analyze the supplied image and determine whether it contains prohibited
content.

IMPORTANT: NUDITY IS ITS OWN CATEGORY.

FLAG "nudity" if ANY of the following are visibly exposed:

- Penis
- Testicles
- Vulva
- Vagina/genital area
- Anus when clearly exposed as nudity
- Bare breasts when nipples/areola are visible
- Clearly exposed nipples
- Full or substantial nude body exposure

This MUST be flagged even when:
- The person is not posing sexually
- There is no sexual activity
- It is a normal photograph
- It is a drawing
- It is anime/manga
- It is CGI
- It is a video game character
- It is an AI-generated image
- It is a GIF
- It is a meme

Do NOT require sexual intent for the "nudity" category.

FLAG "pornography" for:
- Explicit sexual acts
- Sexual intercourse
- Oral sex
- Masturbation
- Explicit sexual contact
- Sexual fluids
- Clearly pornographic imagery

FLAG "gore" for:
- Graphic blood
- Large amounts of blood
- Graphic wounds
- Open wounds
- Exposed organs
- Dismemberment
- Decapitation
- Mutilation
- Corpses shown graphically
- Graphic injury
- Graphic death
- Extreme fictional gore
- Anime/cartoon/game gore
- Drawn or illustrated gore

FLAG "violence" for extreme or graphic violence even if it does not
clearly fit the gore category.

FLAG "self_harm" for graphic self-harm or suicide imagery.

FLAG "animal_gore" for graphic animal injuries, slaughter, mutilation,
animal corpses, or extreme animal cruelty.

FLAG "weapons" when a firearm, knife, explosive, or other weapon is
clearly visible. This category is for detection only; the bot can be
configured to decide what action to take.

Do NOT flag:
- Normal clothing
- Normal swimwear that does not expose prohibited nudity
- Non-sexual medical diagrams
- Harmless red objects
- Ketchup/tomato sauce
- Non-graphic fictional fighting
- A weapon that is not actually visible

For animated media, consider the supplied frame/media carefully.

Return ONLY valid JSON.

Use exactly:

{
  "flagged": true,
  "category": "nudity",
  "reason": "Visible nudity."
}

Allowed categories:

"nudity"
"pornography"
"gore"
"violence"
"self_harm"
"animal_gore"
"weapons"
"other_violation"
"none"

If nothing prohibited is detected:

{
  "flagged": false,
  "category": "none",
  "reason": "No prohibited content detected."
}
"""


class ContentFilter(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = None

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
            title="Content Filter",
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
                value="GEMINI_API_KEY is not configured.",
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
        channel="Channel for moderation logs"
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
                "Use: `delete`, `warn`, `timeout`, or `ban`."
            )

        await db.set_guild_config(
            ctx.guild.id,
            content_filter_action=action
        )

        await ctx.send(
            f"Content filter action set to **{action}**."
        )

    async def _gemini_classify(
        self,
        data: bytes,
        mime_type: str
    ):

        if not self.session:
            return None

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
            ) as response:

                if response.status != 200:
                    return None

                result = await response.json()

        except Exception:
            return None

        try:

            raw = (
                result["candidates"][0]
                ["content"]["parts"][0]
                ["text"]
            )

            parsed = json.loads(raw)

            return {
                "flagged": bool(
                    parsed.get("flagged", False)
                ),
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

    def _extract_gif_frames(self, data: bytes):

        frames = []

        try:

            gif = Image.open(
                io.BytesIO(data)
            )

            frame_count = getattr(
                gif,
                "n_frames",
                1
            )

            if frame_count <= MAX_GIF_FRAMES:

                indexes = range(frame_count)

            else:

                indexes = [
                    round(
                        i * (frame_count - 1)
                        / (MAX_GIF_FRAMES - 1)
                    )
                    for i in range(MAX_GIF_FRAMES)
                ]

            for index in indexes:

                gif.seek(index)

                frame = gif.convert("RGB")

                buffer = io.BytesIO()

                frame.save(
                    buffer,
                    format="JPEG",
                    quality=90
                )

                frames.append(
                    buffer.getvalue()
                )

        except Exception:
            return []

        return frames

    async def _classify_attachment(
        self,
        attachment: discord.Attachment
    ):

        try:

            async with self.session.get(
                attachment.url,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:

                if response.status != 200:
                    return None

                data = await response.read()

        except Exception:
            return None

        if len(data) > MAX_FILE_BYTES:

            return {
                "flagged": False,
                "category": "unscanned",
                "reason": "File exceeds the scan size limit."
            }

        content_type = (
            attachment.content_type or ""
        ).lower()

        filename = attachment.filename.lower()

        # GIF = scan multiple individual frames.
        if (
            content_type == "image/gif"
            or filename.endswith(".gif")
        ):

            frames = self._extract_gif_frames(data)

            if not frames:
                return {
                    "flagged": False,
                    "category": "unscanned",
                    "reason": "Could not extract GIF frames."
                }

            for frame in frames:

                result = await self._gemini_classify(
                    frame,
                    "image/jpeg"
                )

                if result and result.get("flagged"):

                    return result

            return {
                "flagged": False,
                "category": "none",
                "reason": "No prohibited content detected."
            }

        # Normal images.
        if content_type.startswith("image/"):

            return await self._gemini_classify(
                data,
                content_type
            )

        # Videos are sent to Gemini directly.
        if content_type.startswith("video/"):

            return await self._gemini_classify(
                data,
                content_type
            )

        return None

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):

        if (
            message.author.bot
            or message.guild is None
            or not message.attachments
            or not GEMINI_API_KEY
        ):
            return

        row = await db.get_guild_config(
            message.guild.id
        )

        if not row["content_filter_enabled"]:
            return

        for attachment in message.attachments[
            :MAX_ATTACHMENTS_PER_MESSAGE
        ]:

            result = await self._classify_attachment(
                attachment
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

        category = result.get(
            "category",
            "unknown"
        )

        reason = result.get(
            "reason",
            "Flagged by content filter"
        )

        action = (
            row["content_filter_action"]
            or "delete"
        )

        member = message.author

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        if (
            action == "warn"
            and isinstance(member, discord.Member)
        ):

            try:

                await db.add_warning(
                    message.guild.id,
                    member.id,
                    self.bot.user.id,
                    f"Content filter [{category}]: {reason}"
                )

            except Exception:
                pass

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

        log_id = row[
            "content_filter_log_channel_id"
        ]

        if not log_id:
            return

        channel = message.guild.get_channel(
            log_id
        )

        if not channel:
            return

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
    await bot.add_cog(
        ContentFilter(bot)
    )
