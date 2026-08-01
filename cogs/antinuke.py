import discord
from discord import app_commands
from discord.ext import commands

from database import db

# How we punish someone who trips antinuke: strip every role they have and
# ban them. This is intentionally the most severe response, since by the
# time antinuke fires, real damage (bans/kicks/role changes) may already be
# in progress.


class AntiNuke(commands.Cog):
    """Watches the audit log for destructive actions (mass bans/kicks, role
    or vanity changes, unauthorized bot adds) and punishes whoever did it,
    unless they're whitelisted or the server owner."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def is_protected(self, guild: discord.Guild, user: discord.abc.User) -> bool:
        """True if this user should be PUNISHED for a dangerous action, i.e.
        they are NOT the owner, NOT the bot itself, and NOT whitelisted."""
        if user.id == guild.owner_id or user.bot is False and user.id == self.bot.user.id:
            return False
        if user.id == self.bot.user.id:
            return False
        if await db.antinuke_is_whitelisted(guild.id, user.id):
            return False
        return True

    async def punish(self, guild: discord.Guild, user: discord.Member, reason: str):
        config = await db.get_antinuke_config(guild.id)
        try:
            member = guild.get_member(user.id) or await guild.fetch_member(user.id)
            if member:
                roles_to_remove = [r for r in member.roles if r.name != "@everyone" and not r.managed]
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason=f"Antinuke: {reason}")
        except discord.HTTPException:
            pass
        try:
            await guild.ban(user, reason=f"Antinuke: {reason}", delete_message_seconds=0)
        except discord.HTTPException:
            pass

        if config["log_channel_id"]:
            channel = guild.get_channel(config["log_channel_id"])
            if channel:
                embed = discord.Embed(
                    title="🛡️ Antinuke Triggered",
                    description=f"**User:** {user} (`{user.id}`)\n**Action:** {reason}\n**Response:** stripped roles + banned",
                    color=discord.Color.red(),
                )
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException:
                    pass

    # ---------- commands ----------

    @commands.hybrid_group(name="antinuke", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antinuke(self, ctx: commands.Context):
        config = await db.get_antinuke_config(ctx.guild.id)
        embed = discord.Embed(title="Antinuke Configuration", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value="✅" if config["enabled"] else "❌")
        embed.add_field(name="Vanity protection", value="✅" if config["vanity"] else "❌")
        embed.add_field(name="Bot add protection", value="✅" if config["botadd"] else "❌")
        embed.add_field(name="Ban protection", value="✅" if config["ban"] else "❌")
        embed.add_field(name="Kick protection", value="✅" if config["kick"] else "❌")
        embed.add_field(name="Role protection", value="✅" if config["role"] else "❌")
        await ctx.send(embed=embed)

    @antinuke.command(name="enable")
    @commands.has_permissions(administrator=True)
    async def antinuke_enable(self, ctx: commands.Context):
        await db.set_antinuke_config(ctx.guild.id, enabled=1)
        await ctx.send("🛡️ Antinuke enabled.")

    @antinuke.command(name="disable")
    @commands.has_permissions(administrator=True)
    async def antinuke_disable(self, ctx: commands.Context):
        await db.set_antinuke_config(ctx.guild.id, enabled=0)
        await ctx.send("Antinuke disabled.")

    @antinuke.command(name="logs")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel where antinuke actions get logged")
    async def antinuke_logs(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.set_antinuke_config(ctx.guild.id, log_channel_id=channel.id)
        await ctx.send(f"Antinuke logs will be sent to {channel.mention}.")

    @antinuke.command(name="vanity")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def antinuke_vanity(self, ctx: commands.Context, state: str):
        await db.set_antinuke_config(ctx.guild.id, vanity=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Vanity URL protection: **{state}**")

    @antinuke.command(name="botadd")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def antinuke_botadd(self, ctx: commands.Context, state: str):
        await db.set_antinuke_config(ctx.guild.id, botadd=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Bot add protection: **{state}**")

    @antinuke.command(name="ban")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def antinuke_ban(self, ctx: commands.Context, state: str):
        await db.set_antinuke_config(ctx.guild.id, ban=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Ban protection: **{state}**")

    @antinuke.command(name="kick")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def antinuke_kick(self, ctx: commands.Context, state: str):
        await db.set_antinuke_config(ctx.guild.id, kick=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Kick protection: **{state}**")

    @antinuke.command(name="role")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def antinuke_role(self, ctx: commands.Context, state: str):
        await db.set_antinuke_config(ctx.guild.id, role=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Role change protection: **{state}**")

    @antinuke.group(name="whitelist", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antinuke_whitelist(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @antinuke_whitelist.command(name="add")
    @app_commands.describe(member="Member to exempt from antinuke")
    async def antinuke_whitelist_add(self, ctx: commands.Context, member: discord.Member):
        await db.antinuke_whitelist_add(ctx.guild.id, member.id)
        await ctx.send(f"{member.mention} is now whitelisted from antinuke.")

    @antinuke_whitelist.command(name="remove")
    @app_commands.describe(member="Member to remove from the whitelist")
    async def antinuke_whitelist_remove(self, ctx: commands.Context, member: discord.Member):
        await db.antinuke_whitelist_remove(ctx.guild.id, member.id)
        await ctx.send(f"{member.mention} removed from the antinuke whitelist.")

    @antinuke_whitelist.command(name="list")
    async def antinuke_whitelist_list(self, ctx: commands.Context):
        ids = await db.antinuke_whitelist_list(ctx.guild.id)
        if not ids:
            return await ctx.send("No one is whitelisted.")
        await ctx.send("\n".join(f"<@{i}>" for i in ids))

    # ---------- audit log listener ----------

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        guild = entry.guild
        config = await db.get_antinuke_config(guild.id)
        if not config["enabled"]:
            return
        if not await self.is_protected(guild, entry.user):
            return

        action = entry.action

        if config["ban"] and action == discord.AuditLogAction.ban:
            await self.punish(guild, entry.user, "Unauthorized ban")

        elif config["kick"] and action == discord.AuditLogAction.kick:
            await self.punish(guild, entry.user, "Unauthorized kick")

        elif config["role"] and action in (
            discord.AuditLogAction.role_create,
            discord.AuditLogAction.role_delete,
            discord.AuditLogAction.role_update,
        ):
            await self.punish(guild, entry.user, "Unauthorized role change")

        elif config["botadd"] and action == discord.AuditLogAction.bot_add:
            await self.punish(guild, entry.user, "Unauthorized bot add")

        elif config["vanity"] and action == discord.AuditLogAction.guild_update:
            before_vanity = getattr(entry.before, "vanity_url_code", None)
            after_vanity = getattr(entry.after, "vanity_url_code", None)
            if before_vanity != after_vanity:
                await self.punish(guild, entry.user, "Unauthorized vanity URL change")


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
