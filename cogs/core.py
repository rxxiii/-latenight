import discord
from discord import app_commands
from discord.ext import commands

import aiohttp

from database import db


async def say_permission_check(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return True  # in a DM/group chat there's no server permission system to check
    return ctx.author.guild_permissions.manage_messages


class SayReplyModal(discord.ui.Modal, title="Say something"):
    text = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=2000)

    def __init__(self, target_message: discord.Message):
        super().__init__()
        self.target_message = target_message

    async def on_submit(self, interaction: discord.Interaction):
        await self.target_message.reply(str(self.text))
        await interaction.response.send_message("Sent.", ephemeral=True)


class Core(commands.Cog):
    """Prefix management and general utility commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.say_reply_menu = app_commands.ContextMenu(name="Say (reply to this)", callback=self.say_reply_context)
        self.bot.tree.add_command(self.say_reply_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.say_reply_menu.name, type=self.say_reply_menu.type)

    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def say_reply_context(self, interaction: discord.Interaction, message: discord.Message):
        if interaction.guild is not None and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        await interaction.response.send_modal(SayReplyModal(message))


    @commands.hybrid_group(name="create", invoke_without_command=True, description="Create a saved bot settings profile.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def create(self, ctx: commands.Context):
        await ctx.send("Usage: `,create settings <name>`")

    @create.command(name="settings", description="Save this server's bot settings.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(name="Name for the settings profile")
    async def create_settings(self, ctx: commands.Context, name: str):
        name = name.strip()
        if not name or len(name) > 50:
            return await ctx.send("Settings name must be between 1 and 50 characters.")
        if any(ch in name for ch in "\r\n"):
            return await ctx.send("Settings name cannot contain line breaks.")
        await db.create_settings_snapshot(ctx.guild.id, name)
        await ctx.send(f"â Saved all bot configuration settings as `{name}`.")

    @commands.hybrid_group(name="load", invoke_without_command=True, description="Load a saved bot settings profile.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def load(self, ctx: commands.Context):
        await ctx.send("Usage: `,load settings <name>`")

    @load.command(name="settings", description="Load a saved server settings profile.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(name="Name of the settings profile to load")
    async def load_settings(self, ctx: commands.Context, name: str):
        name = name.strip()
        if not name or len(name) > 50:
            return await ctx.send("Settings name must be between 1 and 50 characters.")
        try:
            loaded = await db.load_settings_snapshot(ctx.guild.id, name)
        except Exception:
            return await ctx.send("â I couldn't load that settings profile. Check the bot console for details.")
        if not loaded:
            return await ctx.send(f"â No settings profile named `{name}` exists in this server.")
        await ctx.send(f"â Loaded settings profile `{name}`.")


    @commands.hybrid_group(name="prefix", invoke_without_command=True)
    @commands.guild_only()
    async def prefix(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)
        await ctx.send(f"Current prefix: `{row['prefix']}`")

    @prefix.command(name="set", description="Set the command prefix for this server.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(new_prefix="The new prefix for this server (1-5 characters)")
    async def prefix_set(self, ctx: commands.Context, new_prefix: str):
        new_prefix = new_prefix.strip()
        if not new_prefix:
            return await ctx.send("Prefix cannot be empty. Use 1-5 characters.")
        if len(new_prefix) > 5:
            return await ctx.send("Prefix must be 5 characters or fewer.")
        if any(ch.isspace() for ch in new_prefix):
            return await ctx.send("Prefix cannot contain spaces or line breaks.")

        await db.set_guild_config(ctx.guild.id, prefix=new_prefix)
        await ctx.send(
            f"Prefix updated to `{new_prefix}`. Your old prefix will stop working immediately."
        )

    @commands.hybrid_command(name="say", description="Make the bot say something.")
    @commands.check(say_permission_check)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        message="What the bot should say",
        channel="Channel to send it in (defaults to here)",
        reply_to="Message ID to reply to (optional)",
    )
    async def say(self, ctx: commands.Context, message: str, channel: discord.TextChannel = None, reply_to: str = None):
        channel = channel or ctx.channel

        reference = None
        if reply_to:
            reply_to = reply_to.strip("<>")
            if not reply_to.isdigit():
                await ctx.send("That doesn't look like a valid message ID.", ephemeral=bool(ctx.interaction))
                return
            try:
                reference = await channel.fetch_message(int(reply_to))
            except (discord.NotFound, discord.Forbidden):
                await ctx.send("Couldn't find that message to reply to.", ephemeral=bool(ctx.interaction))
                return
        elif not ctx.interaction and ctx.message.reference:
            # If you reply to a message and then use ,say, it replies to that same message.
            resolved = ctx.message.reference.resolved
            if isinstance(resolved, discord.Message):
                reference = resolved

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
            try:
                # Sending directly (rather than as the interaction's reply)
                # avoids Discord's "X used /say" label on the message.
                await channel.send(message, reference=reference)
                await ctx.interaction.followup.send("Sent.", ephemeral=True)
            except discord.Forbidden:
                # No direct channel access (e.g. a group chat via user-install)
                # â fall back to a normal interaction response. This will show
                # the "used /say" label, but it's the only way it can work here.
                await ctx.interaction.followup.send(message)
        else:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            await channel.send(message, reference=reference)

    @commands.hybrid_command(name="ping", description="Check the bot's latency.")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"Pong! `{round(self.bot.latency * 1000)}ms`")

    @commands.hybrid_command(name="userinfo", description="Show information about a member.")
    @app_commands.describe(member="The member to look up (defaults to you)")
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=str(member), color=discord.Color.blurple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(
            name="Joined server",
            value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Account created",
            value=discord.utils.format_dt(member.created_at, "R"),
            inline=True,
        )
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        embed.add_field(name=f"Roles [{len(roles)}]", value=" ".join(roles) or "None", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Show information about this server.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=str(guild.owner), inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"), inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
        await ctx.send(embed=embed)

    # ---------- server customization ----------

    async def _server_owner_or_bot_owner(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        return ctx.author.id == ctx.guild.owner_id or await self.bot.is_owner(ctx.author)

    @commands.hybrid_group(name="customize", invoke_without_command=True, description="Customize this server's profile.")
    @commands.guild_only()
    async def customize(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @customize.command(name="avatar", description="Customize the server avatar/icon.")
    @app_commands.describe(url="Direct image URL (PNG/JPG/GIF where supported)")
    async def customize_avatar(self, ctx: commands.Context, url: str):
        if not await self._server_owner_or_bot_owner(ctx):
            return await ctx.send("Only the server owner or bot owner/co-owner can use this command.")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.")
                    data = await resp.read()
            await ctx.guild.edit(icon=data, reason=f"Server avatar customized by {ctx.author}")
            await ctx.send("â Server avatar updated.")
        except (discord.HTTPException, ValueError, aiohttp.ClientError) as e:
            await ctx.send(f"Couldn't update the server avatar: {e}")

    @customize.command(name="banner", description="Customize the server banner.")
    @app_commands.describe(url="Direct image URL (PNG/JPG/GIF where supported)")
    async def customize_banner(self, ctx: commands.Context, url: str):
        if not await self._server_owner_or_bot_owner(ctx):
            return await ctx.send("Only the server owner or bot owner/co-owner can use this command.")
        if "BANNER" not in ctx.guild.features:
            return await ctx.send("This server doesn't have the Server Banner feature available.")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.")
                    data = await resp.read()
            await ctx.guild.edit(banner=data, reason=f"Server banner customized by {ctx.author}")
            await ctx.send("â Server banner updated.")
        except (discord.HTTPException, ValueError, aiohttp.ClientError) as e:
            await ctx.send(f"Couldn't update the server banner: {e}")

    @customize.command(name="bio", description="Customize the server bio/description.")
    @app_commands.describe(text="The server bio/description (leave blank to clear it)")
    async def customize_bio(self, ctx: commands.Context, *, text: str = ""):
        if not await self._server_owner_or_bot_owner(ctx):
            return await ctx.send("Only the server owner or bot owner/co-owner can use this command.")
        if "COMMUNITY" not in ctx.guild.features:
            return await ctx.send("Discord only exposes a server description/bio for Community servers.")
        text = text.strip()
        if len(text) > 1200:
            return await ctx.send("The server bio is too long (maximum 1200 characters).")
        try:
            await ctx.guild.edit(description=text or None, reason=f"Server bio customized by {ctx.author}")
            await ctx.send("â Server bio updated.")
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't update the server bio: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
