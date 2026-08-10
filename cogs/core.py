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

    @commands.hybrid_group(name="prefix", invoke_without_command=True)
    @commands.guild_only()
    async def prefix(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)
        await ctx.send(f"Current prefix: `{row['prefix']}`")

    @prefix.command(name="set")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(new_prefix="The new prefix for this server")
    async def prefix_set(self, ctx: commands.Context, new_prefix: str):
        if len(new_prefix) > 5:
            return await ctx.send("Prefix must be 5 characters or fewer.")
        await db.set_guild_config(ctx.guild.id, prefix=new_prefix)
        await ctx.send(f"Prefix updated to `{new_prefix}`.")

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
                # — fall back to a normal interaction response. This will show
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

    # ---------- global bot appearance (owner-only — affects every server) ----------

    @commands.hybrid_command(name="setavatar", description="Change the bot's avatar EVERYWHERE (not just this server).")
    @commands.is_owner()
    @app_commands.describe(url="Direct image URL (png/jpg)")
    async def setavatar(self, ctx: commands.Context, url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.")
                    data = await resp.read()
            await self.bot.user.edit(avatar=data)
            await ctx.send("✅ Avatar updated — this changes the bot everywhere it's added, not just this server.")
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't update the avatar: {e}")

    @commands.hybrid_command(name="setbanner", description="Change the bot's banner EVERYWHERE (not just this server).")
    @commands.is_owner()
    @app_commands.describe(url="Direct image URL (png/jpg)")
    async def setbanner(self, ctx: commands.Context, url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.")
                    data = await resp.read()
            await self.bot.user.edit(banner=data)
            await ctx.send("✅ Banner updated — this changes the bot everywhere it's added, not just this server.")
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't update the banner: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
