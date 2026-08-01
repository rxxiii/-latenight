import datetime
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db


def parse_duration(duration: str) -> datetime.timedelta:
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    unit = duration[-1].lower()
    if unit not in units:
        raise ValueError("Duration must end in s/m/h/d/w, e.g. 10m, 2h, 1d, 7d")
    amount = float(duration[:-1])
    return datetime.timedelta(**{units[unit]: amount})


async def staff_check(ctx: commands.Context) -> bool:
    """Allows the command if the invoker is a bound 'staff' role, or has
    manage_guild/administrator — used for commands like ,invoke and ,jail
    that shouldn't require a specific native Discord permission."""
    if ctx.guild is None:
        return False
    return await db.is_staff_member(ctx.guild.id, ctx.author)


class ModerationExtended(commands.Cog):
    """Setup wizards, staff role binding, tempban/softban/hardban, and jail."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_temp_bans.start()

    def cog_unload(self):
        self.check_temp_bans.cancel()

    # ---------- setup ----------

    @commands.hybrid_command(name="setup", description="Create the bot's mute role and jail role/channel for this server.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def setup_cmd(self, ctx: commands.Context):
        guild = ctx.guild
        row = await db.get_guild_config(guild.id)

        mute_role = guild.get_role(row["mute_role_id"]) if row["mute_role_id"] else None
        if mute_role is None:
            mute_role = await guild.create_role(name="Muted", reason="Bot setup")
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
                except discord.HTTPException:
                    pass
            await db.set_guild_config(guild.id, mute_role_id=mute_role.id)

        jail_role = guild.get_role(row["jail_role_id"]) if row["jail_role_id"] else None
        if jail_role is None:
            jail_role = await guild.create_role(name="Jailed", reason="Bot setup")

        jail_channel = guild.get_channel(row["jail_channel_id"]) if row["jail_channel_id"] else None
        if jail_channel is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                jail_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            jail_channel = await guild.create_text_channel("jail", overwrites=overwrites, reason="Bot setup")
            for channel in guild.channels:
                if channel.id == jail_channel.id:
                    continue
                try:
                    await channel.set_permissions(jail_role, view_channel=False)
                except discord.HTTPException:
                    pass

        await db.set_guild_config(guild.id, jail_role_id=jail_role.id, jail_channel_id=jail_channel.id)
        await ctx.send(f"✅ Setup complete. Mute role: {mute_role.mention}. Jail role: {jail_role.mention} in {jail_channel.mention}.")

    @commands.hybrid_command(name="setupmute", description="(Re)create just the mute role and apply it across all channels.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def setupmute(self, ctx: commands.Context):
        guild = ctx.guild
        mute_role = await guild.create_role(name="Muted", reason="Mute setup")
        for channel in guild.channels:
            try:
                await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
            except discord.HTTPException:
                pass
        await db.set_guild_config(guild.id, mute_role_id=mute_role.id)
        await ctx.send(f"✅ Mute role created: {mute_role.mention}")

    # ---------- staff binding ----------

    @commands.hybrid_group(name="bind", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def bind(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @bind.group(name="staff", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    @app_commands.describe(role="Role to bind as staff")
    async def bind_staff(self, ctx: commands.Context, role: discord.Role = None):
        if role is None:
            return await ctx.invoke(self.bind_staff_list)
        await db.add_staff_role(ctx.guild.id, role.id)
        await ctx.send(f"{role.mention} is now bound as a staff role.")

    @bind_staff.command(name="list")
    async def bind_staff_list(self, ctx: commands.Context):
        role_ids = await db.list_staff_roles(ctx.guild.id)
        if not role_ids:
            return await ctx.send("No staff roles bound yet.")
        mentions = [f"<@&{r}>" for r in role_ids]
        await ctx.send("Staff roles: " + ", ".join(mentions))

    @bind_staff.command(name="remove")
    @app_commands.describe(role="Role to unbind from staff")
    async def bind_staff_remove(self, ctx: commands.Context, role: discord.Role):
        await db.remove_staff_role(ctx.guild.id, role.id)
        await ctx.send(f"{role.mention} removed from staff roles.")

    # ---------- invoke ----------

    @commands.hybrid_command(
        name="invoke",
        description="Manually run the strip-roles-and-ban response on a member (for compromised/suspicious accounts).",
    )
    @commands.check(staff_check)
    @app_commands.describe(member="Member to punish", reason="Why you're invoking this")
    async def invoke_cmd(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Manually invoked by staff"):
        if member.id == ctx.guild.owner_id:
            return await ctx.send("Can't invoke this on the server owner.")
        try:
            roles_to_remove = [r for r in member.roles if r.name != "@everyone" and not r.managed]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
        except discord.HTTPException:
            pass
        try:
            await ctx.guild.ban(member, reason=reason, delete_message_seconds=0)
        except discord.HTTPException:
            return await ctx.send("Removed roles, but I couldn't ban them (check my permissions/role position).")
        await ctx.send(f"⚡ Invoked on **{member}** — roles stripped and banned. Reason: {reason}")

    # ---------- tempban / softban / hardban ----------

    @commands.hybrid_command(name="tempban", description="Ban a member for a set duration, then auto-unban.")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(member="Member to tempban", duration="e.g. 1d, 12h, 1w", reason="Reason for the ban")
    async def tempban(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
        try:
            delta = parse_duration(duration)
        except ValueError as e:
            return await ctx.send(str(e))
        unban_time = int(time.time() + delta.total_seconds())
        await ctx.guild.ban(member, reason=f"{ctx.author}: {reason} (tempban {duration})")
        await db.add_temp_ban(ctx.guild.id, member.id, unban_time)
        await ctx.send(f"🔨 Tempbanned **{member}** for `{duration}` — {reason}")

    @commands.hybrid_command(name="softban", description="Ban then immediately unban a member (clears their recent messages).")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(member="Member to softban", reason="Reason for the softban")
    async def softban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await ctx.guild.ban(member, reason=f"{ctx.author}: softban: {reason}", delete_message_seconds=604800)
        await ctx.guild.unban(member, reason="Softban auto-unban")
        await ctx.send(f"🔨 Softbanned **{member}** (messages purged, they can rejoin) — {reason}")

    @commands.hybrid_command(name="hardban", description="Permanently ban a member — can't be lifted with a normal /unban.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(member="Member to hardban", reason="Reason for the hardban")
    async def hardban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await ctx.guild.ban(member, reason=f"{ctx.author}: hardban: {reason}")
        await db.add_hardban(ctx.guild.id, member.id, reason)
        await ctx.send(f"⛔ Hardbanned **{member}** — {reason}\nThis requires `,hardban remove` before it can be undone.")

    @commands.hybrid_command(name="hardban-remove", description="Lift a hardban so the user can be normally unbanned.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(user_id="ID of the hardbanned user")
    async def hardban_remove(self, ctx: commands.Context, user_id: str):
        await db.remove_hardban(ctx.guild.id, int(user_id))
        await ctx.send(f"Hardban lifted for `{user_id}`. You can now use `,unban` on them.")

    # ---------- jail ----------

    @commands.hybrid_command(name="jail", description="Strip a member's roles and confine them to the jail channel.")
    @commands.check(staff_check)
    @app_commands.describe(member="Member to jail", reason="Reason for jailing")
    async def jail(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        row = await db.get_guild_config(ctx.guild.id)
        if not row["jail_role_id"] or not row["jail_channel_id"]:
            return await ctx.send("Run `,setup` first to create the jail role and channel.")
        jail_role = ctx.guild.get_role(row["jail_role_id"])
        if jail_role is None:
            return await ctx.send("Jail role no longer exists — run `,setup` again.")

        existing = await db.get_jailed(ctx.guild.id, member.id)
        if existing:
            return await ctx.send(f"**{member}** is already jailed.")

        previous_roles = [r.id for r in member.roles if r.name != "@everyone" and not r.managed]
        await db.set_jailed(ctx.guild.id, member.id, ",".join(str(r) for r in previous_roles))

        try:
            if previous_roles:
                await member.remove_roles(*[ctx.guild.get_role(r) for r in previous_roles if ctx.guild.get_role(r)], reason=reason)
            await member.add_roles(jail_role, reason=reason)
        except discord.HTTPException:
            return await ctx.send("I couldn't change that member's roles — check my role position.")

        await ctx.send(f"⛓️ Jailed **{member}** — {reason}")

    @commands.hybrid_command(name="unjail", description="Release a member from jail and restore their previous roles.")
    @commands.check(staff_check)
    @app_commands.describe(member="Member to unjail")
    async def unjail(self, ctx: commands.Context, member: discord.Member):
        row = await db.get_guild_config(ctx.guild.id)
        jail_data = await db.get_jailed(ctx.guild.id, member.id)
        if jail_data is None:
            return await ctx.send(f"**{member}** isn't jailed.")

        jail_role = ctx.guild.get_role(row["jail_role_id"]) if row["jail_role_id"] else None
        previous_role_ids = [int(r) for r in jail_data["previous_roles"].split(",") if r]
        roles_to_restore = [ctx.guild.get_role(r) for r in previous_role_ids if ctx.guild.get_role(r)]

        try:
            if jail_role and jail_role in member.roles:
                await member.remove_roles(jail_role, reason="Unjailed")
            if roles_to_restore:
                await member.add_roles(*roles_to_restore, reason="Unjailed")
        except discord.HTTPException:
            pass

        await db.remove_jailed(ctx.guild.id, member.id)
        await ctx.send(f"🔓 Unjailed **{member}**, roles restored.")

    # ---------- temp ban expiry loop ----------

    @tasks.loop(minutes=1)
    async def check_temp_bans(self):
        expired = await db.get_expired_temp_bans(int(time.time()))
        for entry in expired:
            guild = self.bot.get_guild(entry["guild_id"])
            if guild:
                try:
                    await guild.unban(discord.Object(id=entry["user_id"]), reason="Tempban expired")
                except discord.HTTPException:
                    pass
            await db.remove_temp_ban(entry["id"])

    @check_temp_bans.before_loop
    async def before_check_temp_bans(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationExtended(bot))
