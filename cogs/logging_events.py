import discord
from discord import app_commands
from discord.ext import commands

from database import db

CATEGORIES = ["messages", "members", "roles", "channels", "invites", "emojis", "voice"]


class LoggingEvents(commands.Cog):
    """,log add #channel <category> to route events. Supported categories:
    messages, members, roles, channels, invites, emojis, voice."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_log(self, guild: discord.Guild, category: str, embed: discord.Embed):
        channel_id = await db.get_log_channel(guild.id, category)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    @commands.hybrid_group(name="log", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def log(self, ctx: commands.Context):
        rows = await db.list_log_channels(ctx.guild.id)
        if not rows:
            return await ctx.send(f"No logging configured. Categories: {', '.join(CATEGORIES)}")
        lines = [f"**{r['category']}** → <#{r['channel_id']}>" for r in rows]
        await ctx.send("\n".join(lines))

    @log.command(name="add")
    @app_commands.describe(channel="Channel to send this category's logs to", category=f"One of: {', '.join(CATEGORIES)}")
    async def log_add(self, ctx: commands.Context, channel: discord.TextChannel, category: str):
        category = category.lower()
        if category not in CATEGORIES:
            return await ctx.send(f"Category must be one of: {', '.join(CATEGORIES)}")
        await db.set_log_channel(ctx.guild.id, category, channel.id)
        await ctx.send(f"**{category}** events will now log to {channel.mention}.")

    @log.command(name="remove")
    @app_commands.describe(category=f"One of: {', '.join(CATEGORIES)}")
    async def log_remove(self, ctx: commands.Context, category: str):
        category = category.lower()
        await db.remove_log_channel(ctx.guild.id, category)
        await ctx.send(f"Stopped logging **{category}**.")

    @log.group(name="ignore", invoke_without_command=True)
    async def log_ignore(self, ctx: commands.Context):
        await ctx.invoke(self.log_ignore_list)

    @log_ignore.command(name="add")
    @app_commands.describe(channel="Channel to stop logging message events from")
    async def log_ignore_add(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.log_ignore_add(ctx.guild.id, channel.id)
        await ctx.send(f"{channel.mention} is now ignored by logging.")

    @log_ignore.command(name="remove")
    @app_commands.describe(channel="Channel to stop ignoring")
    async def log_ignore_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.log_ignore_remove(ctx.guild.id, channel.id)
        await ctx.send(f"{channel.mention} is no longer ignored.")

    @log_ignore.command(name="list")
    async def log_ignore_list(self, ctx: commands.Context):
        ids = await db.log_ignore_list(ctx.guild.id)
        if not ids:
            return await ctx.send("No channels ignored.")
        await ctx.send("\n".join(f"<#{i}>" for i in ids))

    # ---------- messages ----------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if await db.is_log_ignored(message.guild.id, message.channel.id):
            return
        embed = discord.Embed(
            title="🗑️ Message Deleted",
            description=message.content or "*(no text content)*",
            color=discord.Color.red(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=message.channel.mention)
        await self._send_log(message.guild, "messages", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.guild is None or before.content == after.content:
            return
        if await db.is_log_ignored(before.guild.id, before.channel.id):
            return
        embed = discord.Embed(title="✏️ Message Edited", color=discord.Color.orange())
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.add_field(name="Before", value=(before.content or "*(empty)*")[:1024], inline=False)
        embed.add_field(name="After", value=(after.content or "*(empty)*")[:1024], inline=False)
        embed.add_field(name="Channel", value=before.channel.mention)
        await self._send_log(before.guild, "messages", embed)

    # ---------- members ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        embed = discord.Embed(title="📥 Member Joined", description=f"{member.mention} (`{member.id}`)", color=discord.Color.green())
        await self._send_log(member.guild, "members", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        embed = discord.Embed(title="📤 Member Left", description=f"{member} (`{member.id}`)", color=discord.Color.red())
        await self._send_log(member.guild, "members", embed)

    # ---------- roles ----------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        embed = discord.Embed(title="✨ Role Created", description=role.mention, color=discord.Color.green())
        await self._send_log(role.guild, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        embed = discord.Embed(title="🗑️ Role Deleted", description=f"**{role.name}**", color=discord.Color.red())
        await self._send_log(role.guild, "roles", embed)

    # ---------- channels ----------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        embed = discord.Embed(title="📁 Channel Created", description=getattr(channel, "mention", channel.name), color=discord.Color.green())
        await self._send_log(channel.guild, "channels", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        embed = discord.Embed(title="🗑️ Channel Deleted", description=f"**#{channel.name}**", color=discord.Color.red())
        await self._send_log(channel.guild, "channels", embed)

    # ---------- invites ----------

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        embed = discord.Embed(
            title="🔗 Invite Created",
            description=f"`{invite.code}` by {invite.inviter.mention if invite.inviter else 'unknown'} in {invite.channel.mention}",
            color=discord.Color.green(),
        )
        await self._send_log(invite.guild, "invites", embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        embed = discord.Embed(title="🔗 Invite Deleted", description=f"`{invite.code}`", color=discord.Color.red())
        await self._send_log(invite.guild, "invites", embed)

    # ---------- emojis ----------

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        added = [e for e in after if e not in before]
        removed = [e for e in before if e not in after]
        if not added and not removed:
            return
        desc = ""
        if added:
            desc += "Added: " + " ".join(str(e) for e in added) + "\n"
        if removed:
            desc += "Removed: " + ", ".join(e.name for e in removed)
        embed = discord.Embed(title="😀 Emojis Updated", description=desc, color=discord.Color.blurple())
        await self._send_log(guild, "emojis", embed)

    # ---------- voice ----------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return
        if before.channel is None:
            desc = f"{member.mention} joined {after.channel.mention}"
        elif after.channel is None:
            desc = f"{member.mention} left {before.channel.mention}"
        else:
            desc = f"{member.mention} moved {before.channel.mention} → {after.channel.mention}"
        embed = discord.Embed(title="🔊 Voice Update", description=desc, color=discord.Color.blurple())
        await self._send_log(member.guild, "voice", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingEvents(bot))
