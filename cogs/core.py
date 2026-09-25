import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from database import db


# ============================================================
# SAY PERMISSION CHECK
# ============================================================

async def say_permission_check(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return True

    return ctx.author.guild_permissions.manage_messages


# ============================================================
# SAY REPLY CONTEXT MENU
# ============================================================

class SayReplyModal(
    discord.ui.Modal,
    title="Say something",
):
    text = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, target_message: discord.Message):
        super().__init__()
        self.target_message = target_message

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            await self.target_message.reply(
                str(self.text)
            )

            await interaction.response.send_message(
                "Sent.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to reply to that message.",
                ephemeral=True,
            )

        except discord.NotFound:
            await interaction.response.send_message(
                "That message no longer exists.",
                ephemeral=True,
            )

        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord returned an error while sending the message.",
                ephemeral=True,
            )


# ============================================================
# CORE COG
# ============================================================

class Core(commands.Cog):
    """
    Prefix management and general utility commands.

    Important:
    create/load/customize are intentionally PREFIX-ONLY.
    This reduces the number of global application commands
    while keeping the commands fully functional.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.say_reply_menu = app_commands.ContextMenu(
            name="Say (reply to this)",
            callback=self.say_reply_context,
        )

        self.bot.tree.add_command(
            self.say_reply_menu
        )

    async def cog_unload(self):
        try:
            self.bot.tree.remove_command(
                self.say_reply_menu.name,
                type=self.say_reply_menu.type,
            )
        except Exception:
            pass

    # ========================================================
    # SAY CONTEXT MENU
    # ========================================================

    async def say_reply_context(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ):
        if (
            interaction.guild is not None
            and not interaction.user.guild_permissions.manage_messages
        ):
            await interaction.response.send_message(
                "You don't have permission to use this.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            SayReplyModal(message)
        )

    # ========================================================
    # CREATE SETTINGS
    # PREFIX ONLY
    # ========================================================

    @commands.group(
        name="create",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_guild=True
    )
    async def create(
        self,
        ctx: commands.Context,
    ):
        await ctx.send(
            "Usage: `,create settings <name>`"
        )

    @create.command(
        name="settings"
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_guild=True
    )
    async def create_settings(
        self,
        ctx: commands.Context,
        *,
        name: str,
    ):
        name = name.strip()

        if not name or len(name) > 50:
            await ctx.send(
                "Settings name must be between "
                "1 and 50 characters."
            )
            return

        if any(
            character in name
            for character in "\r\n"
        ):
            await ctx.send(
                "Settings name cannot contain "
                "line breaks."
            )
            return

        try:
            await db.create_settings_snapshot(
                ctx.guild.id,
                name,
            )

        except Exception:
            await ctx.send(
                "❌ I couldn't save that settings profile. "
                "Check the bot console for details."
            )
            return

        await ctx.send(
            f"✅ Saved all bot configuration settings "
            f"as `{name}`."
        )

    # ========================================================
    # LOAD SETTINGS
    # PREFIX ONLY
    # ========================================================

    @commands.group(
        name="load",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_guild=True
    )
    async def load(
        self,
        ctx: commands.Context,
    ):
        await ctx.send(
            "Usage: `,load settings <name>`"
        )

    @load.command(
        name="settings"
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_guild=True
    )
    async def load_settings(
        self,
        ctx: commands.Context,
        *,
        name: str,
    ):
        name = name.strip()

        if not name or len(name) > 50:
            await ctx.send(
                "Settings name must be between "
                "1 and 50 characters."
            )
            return

        try:
            loaded = await db.load_settings_snapshot(
                ctx.guild.id,
                name,
            )

        except Exception:
            await ctx.send(
                "❌ I couldn't load that settings profile. "
                "Check the bot console for details."
            )
            return

        if not loaded:
            await ctx.send(
                f"❌ No settings profile named "
                f"`{name}` exists in this server."
            )
            return

        await ctx.send(
            f"✅ Loaded settings profile `{name}`."
        )

    # ========================================================
    # PREFIX
    # HYBRID — REMAINS SLASH + PREFIX
    # ========================================================

    @commands.hybrid_group(
        name="prefix",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def prefix(
        self,
        ctx: commands.Context,
    ):
        try:
            row = await db.get_guild_config(
                ctx.guild.id
            )

            current_prefix = (
                row["prefix"]
                if row and row["prefix"]
                else ","
            )

        except Exception:
            current_prefix = ","

        await ctx.send(
            f"Current prefix: `{current_prefix}`"
        )

    @prefix.command(
        name="set",
        description="Set the command prefix for this server.",
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_guild=True
    )
    @app_commands.checks.has_permissions(
        manage_guild=True
    )
    @app_commands.describe(
        new_prefix=(
            "The new prefix for this server "
            "(1-5 characters)"
        )
    )
    async def prefix_set(
        self,
        ctx: commands.Context,
        new_prefix: str,
    ):
        new_prefix = new_prefix.strip()

        if not new_prefix:
            await ctx.send(
                "Prefix cannot be empty. "
                "Use 1-5 characters."
            )
            return

        if len(new_prefix) > 5:
            await ctx.send(
                "Prefix must be 5 characters or fewer."
            )
            return

        if any(
            character.isspace()
            for character in new_prefix
        ):
            await ctx.send(
                "Prefix cannot contain spaces "
                "or line breaks."
            )
            return

        try:
            await db.set_guild_config(
                ctx.guild.id,
                prefix=new_prefix,
            )

        except Exception:
            await ctx.send(
                "❌ I couldn't update the prefix. "
                "Check the bot console for details."
            )
            return

        await ctx.send(
            f"Prefix updated to `{new_prefix}`. "
            f"Your old prefix will stop working immediately."
        )

    # ========================================================
    # SAY
    # HYBRID — REMAINS SLASH + PREFIX
    # ========================================================

    @commands.hybrid_command(
        name="say",
        description="Make the bot say something.",
    )
    @commands.check(
        say_permission_check
    )
    @app_commands.allowed_installs(
        guilds=True,
        users=True,
    )
    @app_commands.allowed_contexts(
        guilds=True,
        dms=True,
        private_channels=True,
    )
    @app_commands.describe(
        message="What the bot should say",
        channel=(
            "Channel to send it in "
            "(defaults to here)"
        ),
        reply_to=(
            "Message ID to reply to "
            "(optional)"
        ),
    )
    async def say(
        self,
        ctx: commands.Context,
        message: str,
        channel: discord.TextChannel = None,
        reply_to: str = None,
    ):
        channel = channel or ctx.channel

        reference = None

        # ----------------------------------------------------
        # Explicit reply_to
        # ----------------------------------------------------

        if reply_to:
            reply_to = reply_to.strip("<>")

            if not reply_to.isdigit():
                await ctx.send(
                    "That doesn't look like a valid message ID.",
                    ephemeral=bool(ctx.interaction),
                )
                return

            try:
                reference = await channel.fetch_message(
                    int(reply_to)
                )

            except discord.NotFound:
                await ctx.send(
                    "Couldn't find that message to reply to.",
                    ephemeral=bool(ctx.interaction),
                )
                return

            except discord.Forbidden:
                await ctx.send(
                    "I don't have permission to read that message.",
                    ephemeral=bool(ctx.interaction),
                )
                return

            except discord.HTTPException:
                await ctx.send(
                    "Discord returned an error while fetching that message.",
                    ephemeral=bool(ctx.interaction),
                )
                return

        # ----------------------------------------------------
        # Prefix command used as a reply
        # ----------------------------------------------------

        elif (
            not ctx.interaction
            and ctx.message.reference
        ):
            resolved = (
                ctx.message.reference.resolved
            )

            if isinstance(
                resolved,
                discord.Message,
            ):
                reference = resolved

        # ----------------------------------------------------
        # Slash command
        # ----------------------------------------------------

        if ctx.interaction:

            try:
                await ctx.interaction.response.defer(
                    ephemeral=True
                )

                await channel.send(
                    message,
                    reference=reference,
                )

                await ctx.interaction.followup.send(
                    "Sent.",
                    ephemeral=True,
                )

            except discord.Forbidden:
                await ctx.interaction.followup.send(
                    "I don't have permission to send "
                    "messages in that channel.",
                    ephemeral=True,
                )

            except discord.NotFound:
                await ctx.interaction.followup.send(
                    "That channel or reply message no longer exists.",
                    ephemeral=True,
                )

            except discord.HTTPException:
                await ctx.interaction.followup.send(
                    "Discord returned an error while sending the message.",
                    ephemeral=True,
                )

            return

        # ----------------------------------------------------
        # Prefix command
        # ----------------------------------------------------

        try:
            await ctx.message.delete()

        except (
            discord.Forbidden,
            discord.NotFound,
            discord.HTTPException,
        ):
            pass

        try:
            await channel.send(
                message,
                reference=reference,
            )

        except discord.Forbidden:
            await ctx.send(
                "I don't have permission to send messages "
                "in that channel."
            )

        except discord.HTTPException:
            await ctx.send(
                "Discord returned an error while sending "
                "the message."
            )

    # ========================================================
    # PING
    # HYBRID
    # ========================================================

    @commands.hybrid_command(
        name="ping",
        description="Check the bot's latency.",
    )
    async def ping(
        self,
        ctx: commands.Context,
    ):
        latency = round(
            self.bot.latency * 1000
        )

        await ctx.send(
            f"Pong! `{latency}ms`"
        )

    # ========================================================
    # USERINFO
    # HYBRID
    # ========================================================

    @commands.hybrid_command(
        name="userinfo",
        description="Show information about a member.",
    )
    @app_commands.describe(
        member=(
            "The member to look up "
            "(defaults to you)"
        )
    )
    async def userinfo(
        self,
        ctx: commands.Context,
        member: discord.Member = None,
    ):
        member = member or ctx.author

        embed = discord.Embed(
            title=str(member),
            color=discord.Color.blurple(),
        )

        embed.set_thumbnail(
            url=member.display_avatar.url
        )

        embed.add_field(
            name="ID",
            value=str(member.id),
            inline=True,
        )

        embed.add_field(
            name="Joined server",
            value=(
                discord.utils.format_dt(
                    member.joined_at,
                    "R",
                )
                if member.joined_at
                else "Unknown"
            ),
            inline=True,
        )

        embed.add_field(
            name="Account created",
            value=discord.utils.format_dt(
                member.created_at,
                "R",
            ),
            inline=True,
        )

        roles = [
            role.mention
            for role in reversed(member.roles)
            if role.name != "@everyone"
        ]

        embed.add_field(
            name=f"Roles [{len(roles)}]",
            value=(
                " ".join(roles)
                if roles
                else "None"
            ),
            inline=False,
        )

        await ctx.send(
            embed=embed
        )

    # ========================================================
    # SERVERINFO
    # HYBRID
    # ========================================================

    @commands.hybrid_command(
        name="serverinfo",
        description="Show information about this server.",
    )
    @commands.guild_only()
    async def serverinfo(
        self,
        ctx: commands.Context,
    ):
        guild = ctx.guild

        embed = discord.Embed(
            title=guild.name,
            color=discord.Color.blurple(),
        )

        if guild.icon:
            embed.set_thumbnail(
                url=guild.icon.url
            )

        embed.add_field(
            name="Owner",
            value=(
                str(guild.owner)
                if guild.owner
                else f"<@{guild.owner_id}>"
            ),
            inline=True,
        )

        embed.add_field(
            name="Members",
            value=str(
                guild.member_count
                or 0
            ),
            inline=True,
        )

        embed.add_field(
            name="Created",
            value=discord.utils.format_dt(
                guild.created_at,
                "R",
            ),
            inline=True,
        )

        embed.add_field(
            name="Roles",
            value=str(
                len(guild.roles)
            ),
            inline=True,
        )

        embed.add_field(
            name="Channels",
            value=str(
                len(guild.channels)
            ),
            inline=True,
        )

        embed.add_field(
            name="Boosts",
            value=str(
                guild.premium_subscription_count
                or 0
            ),
            inline=True,
        )

        await ctx.send(
            embed=embed
        )

    # ========================================================
    # SERVER CUSTOMIZATION
    # PREFIX ONLY
    # ========================================================

    async def _server_owner_or_bot_owner(
        self,
        ctx: commands.Context,
    ) -> bool:
        if ctx.guild is None:
            return False

        if ctx.author.id == ctx.guild.owner_id:
            return True

        return await self.bot.is_owner(
            ctx.author
        )

    async def _download_image(
        self,
        url: str,
    ) -> bytes | None:

        if not url.startswith(
            (
                "http://",
                "https://",
            )
        ):
            return None

        timeout = aiohttp.ClientTimeout(
            total=15
        )

        try:
            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:

                async with session.get(
                    url,
                    allow_redirects=True,
                ) as response:

                    if response.status != 200:
                        return None

                    content_type = (
                        response.headers.get(
                            "Content-Type",
                            "",
                        ).lower()
                    )

                    if not content_type.startswith(
                        "image/"
                    ):
                        return None

                    data = await response.read()

                    # Prevent accidentally downloading
                    # extremely large files.
                    if len(data) > 10 * 1024 * 1024:
                        return None

                    return data

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ):
            return None

    @commands.group(
        name="customize",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def customize(
        self,
        ctx: commands.Context,
    ):
        await ctx.send_help(
            ctx.command
        )

    @customize.command(
        name="avatar"
    )
    async def customize_avatar(
        self,
        ctx: commands.Context,
        url: str,
    ):
        if not await self._server_owner_or_bot_owner(
            ctx
        ):
            await ctx.send(
                "Only the server owner or bot owner/co-owner "
                "can use this command."
            )
            return

        data = await self._download_image(
            url
        )

        if data is None:
            await ctx.send(
                "Couldn't download that image. "
                "Use a direct image URL."
            )
            return

        try:
            await ctx.guild.edit(
                icon=data,
                reason=(
                    f"Server avatar customized "
                    f"by {ctx.author}"
                ),
            )

        except discord.Forbidden:
            await ctx.send(
                "I don't have permission to change "
                "the server avatar."
            )
            return

        except discord.HTTPException as exc:
            await ctx.send(
                f"Couldn't update the server avatar: {exc}"
            )
            return

        await ctx.send(
            "✅ Server avatar updated."
        )

    @customize.command(
        name="banner"
    )
    async def customize_banner(
        self,
        ctx: commands.Context,
        url: str,
    ):
        if not await self._server_owner_or_bot_owner(
            ctx
        ):
            await ctx.send(
                "Only the server owner or bot owner/co-owner "
                "can use this command."
            )
            return

        if "BANNER" not in ctx.guild.features:
            await ctx.send(
                "This server doesn't have the "
                "Server Banner feature available."
            )
            return

        data = await self._download_image(
            url
        )

        if data is None:
            await ctx.send(
                "Couldn't download that image. "
                "Use a direct image URL."
            )
            return

        try:
            await ctx.guild.edit(
                banner=data,
                reason=(
                    f"Server banner customized "
                    f"by {ctx.author}"
                ),
            )

        except discord.Forbidden:
            await ctx.send(
                "I don't have permission to change "
                "the server banner."
            )
            return

        except discord.HTTPException as exc:
            await ctx.send(
                f"Couldn't update the server banner: {exc}"
            )
            return

        await ctx.send(
            "✅ Server banner updated."
        )

    @customize.command(
        name="bio"
    )
    async def customize_bio(
        self,
        ctx: commands.Context,
        *,
        text: str = "",
    ):
        if not await self._server_owner_or_bot_owner(
            ctx
        ):
            await ctx.send(
                "Only the server owner or bot owner/co-owner "
                "can use this command."
            )
            return

        if "COMMUNITY" not in ctx.guild.features:
            await ctx.send(
                "Discord only exposes a server "
                "description/bio for Community servers."
            )
            return

        text = text.strip()

        if len(text) > 1200:
            await ctx.send(
                "The server bio is too long "
                "(maximum 1200 characters)."
            )
            return

        try:
            await ctx.guild.edit(
                description=text or None,
                reason=(
                    f"Server bio customized "
                    f"by {ctx.author}"
                ),
            )

        except discord.Forbidden:
            await ctx.send(
                "I don't have permission to change "
                "the server bio."
            )
            return

        except discord.HTTPException as exc:
            await ctx.send(
                f"Couldn't update the server bio: {exc}"
            )
            return

        await ctx.send(
            "✅ Server bio updated."
        )


# ============================================================
# SETUP
# ============================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        Core(bot)
    )
