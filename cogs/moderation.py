import datetime

import discord
from discord import app_commands
from discord.ext import commands

from database import db


def parse_duration(duration: str) -> datetime.timedelta:
    """Parse strings like '10m', '2h', '1d' into a timedelta."""
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    unit = duration[-1].lower()
    if unit not in units:
        raise ValueError("Duration must end in s/m/h/d/w, e.g. 10m, 2h, 1d")
    amount = float(duration[:-1])
    return datetime.timedelta(**{units[unit]: amount})


class Moderation(commands.Cog):
    """Ban, kick, mute, warn, purge, and channel lock commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context):
        return ctx.guild is not None

    # ---------- ban / kick ----------

    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(member="Member to ban", reason="Reason for the ban")
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send("You can't ban someone with an equal or higher role than you.")
        await member.ban(reason=f"{ctx.author}: {reason}")
        await ctx.send(f"🔨 Banned **{member}** — {reason}")

    @commands.hybrid_command(name="unban", description="Unban a user by ID.")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(user_id="The ID of the user to unban")
    async def unban(self, ctx: commands.Context, user_id: str):
        user = discord.Object(id=int(user_id))
        await ctx.guild.unban(user)
        await ctx.send(f"✅ Unbanned user `{user_id}`.")

    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send("You can't kick someone with an equal or higher role than you.")
        await member.kick(reason=f"{ctx.author}: {reason}")
        await ctx.send(f"👢 Kicked **{member}** — {reason}")

    # ---------- mute (timeout) ----------

    @commands.hybrid_command(name="mute", description="Timeout a member for a duration (e.g. 10m, 2h, 1d).")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    @app_commands.describe(member="Member to mute", duration="e.g. 10m, 2h, 1d", reason="Reason for the mute")
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        try:
            delta = parse_duration(duration)
        except ValueError as e:
            return await ctx.send(str(e))
        if delta > datetime.timedelta(days=28):
            return await ctx.send("Timeouts can be at most 28 days.")
        await member.timeout(delta, reason=f"{ctx.author}: {reason}")
        await ctx.send(f"🔇 Muted **{member}** for `{duration}` — {reason}")

    @commands.hybrid_command(name="unmute", description="Remove a member's timeout.")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    @app_commands.describe(member="Member to unmute")
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        await ctx.send(f"🔊 Unmuted **{member}**.")

    # ---------- warnings ----------

    @commands.hybrid_command(name="warn", description="Warn a member.")
    @commands.has_permissions(moderate_members=True)
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await db.add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
        await ctx.send(f"⚠️ Warned **{member}** — {reason}")
        try:
            await member.send(f"You were warned in **{ctx.guild.name}**: {reason}")
        except discord.Forbidden:
            pass

    @commands.hybrid_command(name="warnings", description="List a member's warnings.")
    @app_commands.describe(member="Member to check")
    async def warnings(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        rows = await db.get_warnings(ctx.guild.id, member.id)
        if not rows:
            return await ctx.send(f"**{member}** has no warnings.")
        embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.orange())
        for i, row in enumerate(rows[:15], start=1):
            mod = ctx.guild.get_member(row["moderator_id"])
            embed.add_field(
                name=f"#{i}",
                value=f"{row['reason']}\n— by {mod.mention if mod else row['moderator_id']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="clearwarnings", description="Clear all warnings for a member.")
    @commands.has_permissions(moderate_members=True)
    @app_commands.describe(member="Member whose warnings to clear")
    async def clearwarnings(self, ctx: commands.Context, member: discord.Member):
        await db.clear_warnings(ctx.guild.id, member.id)
        await ctx.send(f"Cleared warnings for **{member}**.")

    # ---------- purge ----------

    @commands.hybrid_command(name="purge", description="Bulk delete messages in this channel.")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    @app_commands.describe(amount="Number of messages to delete (max 100)")
    async def purge(self, ctx: commands.Context, amount: app_commands.Range[int, 1, 100]):
        await ctx.defer(ephemeral=True) if ctx.interaction else None
        deleted = await ctx.channel.purge(limit=amount + (0 if ctx.interaction else 1))
        msg = await ctx.send(f"🧹 Deleted {len(deleted)} messages.")
        if not ctx.interaction:
            await msg.delete(delay=4)

    # ---------- lock / unlock / slowmode ----------

    @commands.hybrid_command(name="lock", description="Lock the current channel (deny @everyone Send Messages).")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(f"🔒 Locked {channel.mention}.")

    @commands.hybrid_command(name="unlock", description="Unlock the current channel.")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(f"🔓 Unlocked {channel.mention}.")

    @commands.hybrid_command(name="slowmode", description="Set slowmode delay for this channel (seconds).")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(seconds="Delay in seconds (0 to disable, max 21600)")
    async def slowmode(self, ctx: commands.Context, seconds: app_commands.Range[int, 0, 21600]):
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("Slowmode disabled.")
        else:
            await ctx.send(f"🐌 Slowmode set to {seconds}s.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))

