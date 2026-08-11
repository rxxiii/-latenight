import datetime
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db
from cogs.fakeperms import fake_or_real_permission


def parse_duration(duration: str) -> datetime.timedelta:
    """Parse strings like '10m', '2h', '1d' into a timedelta."""
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    unit = duration[-1].lower()
    if unit not in units:
        raise ValueError("not a duration")
    amount = float(duration[:-1])
    return datetime.timedelta(**{units[unit]: amount})


def hierarchy_ok(ctx: commands.Context, target: discord.Member):
    """Returns (True, '') if ctx.author is allowed to act on target, else
    (False, <reason>). Blocks acting on the server owner or on anyone with
    an equal/higher top role, unless the invoker IS the owner."""
    if target.id == ctx.guild.owner_id:
        return False, "You can't take action on the server owner."
    if ctx.author.id == ctx.guild.owner_id:
        return True, ""
    if target.top_role >= ctx.author.top_role:
        return False, "You can't take action on someone with an equal or higher role than you."
    return True, ""


class Moderation(commands.Cog):
    """Ban, kick, timeout, warn, purge, nickname, and channel lock commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_nuke_schedule.start()

    def cog_unload(self):
        self.check_nuke_schedule.cancel()

    async def cog_check(self, ctx: commands.Context):
        return ctx.guild is not None

    # ---------- ban / kick ----------

    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @fake_or_real_permission("ban_members")
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(
        member="Member to ban",
        delete_history="How much recent message history to delete, e.g. 1d, 7d (optional)",
        reason="Reason for the ban",
    )
    async def ban(self, ctx: commands.Context, member: discord.Member, delete_history: str = "0s", *, reason: str = "No reason provided"):
        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error)

        try:
            delete_seconds = int(parse_duration(delete_history).total_seconds())
        except ValueError:
            # They probably didn't mean to pass a duration at all — treat that
            # word as the start of the reason instead, e.g. ",ban @user spamming"
            reason = f"{delete_history} {reason}".strip()
            delete_seconds = 0
        delete_seconds = max(0, min(delete_seconds, 604800))

        await member.ban(reason=f"{ctx.author}: {reason}", delete_message_seconds=delete_seconds)
        await db.log_mod_action(ctx.guild.id, ctx.author.id, "ban", int(time.time()))
        await ctx.send(f"🔨 Banned **{member}** — {reason}")

    @commands.hybrid_command(name="unban", description="Unban a user by ID.")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    @app_commands.describe(user_id="The ID of the user to unban")
    async def unban(self, ctx: commands.Context, user_id: str):
        user_id = user_id.strip("<@!>")
        if not user_id.isdigit():
            return await ctx.send("That doesn't look like a valid user ID.")
        if await db.is_hardbanned(ctx.guild.id, int(user_id)):
            return await ctx.send(
                "This user is hardbanned. Use `,hardban remove` first if you're sure you want to unban them."
            )
        user = discord.Object(id=int(user_id))
        await ctx.guild.unban(user)
        await ctx.send(f"✅ Unbanned user `{user_id}`.")

    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @fake_or_real_permission("kick_members")
    @commands.bot_has_permissions(kick_members=True)
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error)
        await member.kick(reason=f"{ctx.author}: {reason}")
        await db.log_mod_action(ctx.guild.id, ctx.author.id, "kick", int(time.time()))
        await ctx.send(f"👢 Kicked **{member}** — {reason}")

    # ---------- timeout ----------

    @commands.hybrid_command(name="timeout", aliases=["mute"], description="Timeout a member for a duration (e.g. 10m, 2h, 1d).")
    @fake_or_real_permission("moderate_members")
    @commands.bot_has_permissions(moderate_members=True)
    @app_commands.describe(member="Member to timeout", duration="e.g. 10m, 2h, 1d", reason="Reason for the timeout")
    async def timeout_cmd(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error)
        try:
            delta = parse_duration(duration)
        except ValueError:
            return await ctx.send("Duration must end in s/m/h/d/w, e.g. 10m, 2h, 1d")
        if delta > datetime.timedelta(days=28):
            return await ctx.send("Timeouts can be at most 28 days.")
        await member.timeout(delta, reason=f"{ctx.author}: {reason}")
        await db.log_mod_action(ctx.guild.id, ctx.author.id, "timeout", int(time.time()))
        await ctx.send(f"🔇 Timed out **{member}** for `{duration}` — {reason}")

    @commands.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove a member's timeout.")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    @app_commands.describe(member="Member to remove timeout from", reason="Reason")
    async def untimeout_cmd(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await member.timeout(None, reason=f"{ctx.author}: {reason}")
        await ctx.send(f"🔊 Removed timeout for **{member}**. {reason}")

    # ---------- warnings ----------

    @commands.hybrid_command(name="warn", description="Warn a member.")
    @fake_or_real_permission("moderate_members")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error)
        await db.add_warning(ctx.guild.id, member.id, ctx.author.id, reason)
        await db.log_mod_action(ctx.guild.id, ctx.author.id, "warn", int(time.time()))
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

    # ---------- nickname ----------

    @commands.hybrid_command(name="nickname", aliases=["nick"], description="Change (or reset) a member's nickname.")
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    @app_commands.describe(member="Member to rename", new_nickname="Leave blank to reset to their username")
    async def nickname(self, ctx: commands.Context, member: discord.Member, *, new_nickname: str = None):
        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error)
        await member.edit(nick=new_nickname, reason=f"Changed by {ctx.author}")
        if new_nickname:
            await ctx.send(f"Nickname for {member.mention} set to **{new_nickname}**.")
        else:
            await ctx.send(f"Nickname for {member.mention} reset.")

    @commands.hybrid_command(name="forcenickname", aliases=["fn"], description="Lock a member's nickname — reverts it if they try to change it.")
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    @app_commands.describe(member="Member to lock", new_nickname="Nickname to lock them to (leave blank to unlock)")
    async def forcenickname(self, ctx: commands.Context, member: discord.Member, *, new_nickname: str = None):
        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error)
        if new_nickname:
            await member.edit(nick=new_nickname, reason=f"Force-nicknamed by {ctx.author}")
            await db.set_forced_nickname(ctx.guild.id, member.id, new_nickname)
            await ctx.send(f"🔒 {member.mention}'s nickname is locked to **{new_nickname}**.")
        else:
            await db.remove_forced_nickname(ctx.guild.id, member.id)
            await ctx.send(f"🔓 {member.mention}'s nickname is no longer locked.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick == after.nick:
            return
        forced = await db.get_forced_nickname(after.guild.id, after.id)
        if forced and after.nick != forced["nickname"]:
            try:
                await after.edit(nick=forced["nickname"], reason="Nickname is locked")
            except discord.HTTPException:
                pass

    # ---------- purge ----------

    @commands.hybrid_command(name="purge", description="Bulk delete messages in this channel.")
    @fake_or_real_permission("manage_messages")
    @commands.bot_has_permissions(manage_messages=True)
    @app_commands.describe(amount="Number of messages to delete (max 100)")
    async def purge(self, ctx: commands.Context, amount: commands.Range[int, 1, 100]):
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.interaction.followup.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)
        else:
            # +1 so the invoking ",purge N" message itself also gets swept up
            deleted = await ctx.channel.purge(limit=amount + 1)
            msg = await ctx.send(f"🧹 Deleted {len(deleted)} messages.")
            await msg.delete(delay=4)

    # ---------- lock / unlock / slowmode ----------

    async def _lockable_roles(self, guild: discord.Guild):
        staff_role_ids = set(await db.list_staff_roles(guild.id))
        return [r for r in guild.roles if not r.managed and r.id not in staff_role_ids]

    @commands.hybrid_command(name="lock", description="Lock the current channel (or 'all') — denies every role except bound staff roles.")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(target="A channel to lock, 'all' to lock every channel, or leave blank for this channel")
    async def lock(self, ctx: commands.Context, target: str = None):
        roles = await self._lockable_roles(ctx.guild)

        if target and target.lower() == "all":
            channel_count = 0
            for channel in ctx.guild.text_channels:
                changed = False
                for role in roles:
                    overwrite = channel.overwrites_for(role)
                    if overwrite.send_messages is False:
                        continue
                    overwrite.send_messages = False
                    try:
                        await channel.set_permissions(role, overwrite=overwrite)
                        changed = True
                    except discord.HTTPException:
                        pass
                if changed:
                    channel_count += 1
            return await ctx.send(f"🔒 Locked {channel_count} channel(s) for every non-staff role.")

        channel = ctx.channel
        if target:
            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
            except commands.BadArgument:
                return await ctx.send(f"Couldn't find a channel matching `{target}`.")

        for role in roles:
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = False
            try:
                await channel.set_permissions(role, overwrite=overwrite)
            except discord.HTTPException:
                pass
        await ctx.send(f"🔒 Locked {channel.mention} for every non-staff role.")

    @commands.hybrid_command(name="unlock", description="Unlock the current channel, or every channel with 'all'.")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(target="A channel to unlock, 'all' to unlock every channel, or leave blank for this channel")
    async def unlock(self, ctx: commands.Context, target: str = None):
        roles = await self._lockable_roles(ctx.guild)

        if target and target.lower() == "all":
            channel_count = 0
            for channel in ctx.guild.text_channels:
                changed = False
                for role in roles:
                    overwrite = channel.overwrites_for(role)
                    if overwrite.send_messages is not False:
                        continue
                    overwrite.send_messages = None
                    try:
                        await channel.set_permissions(role, overwrite=overwrite)
                        changed = True
                    except discord.HTTPException:
                        pass
                if changed:
                    channel_count += 1
            return await ctx.send(f"🔓 Unlocked {channel_count} channel(s).")

        channel = ctx.channel
        if target:
            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
            except commands.BadArgument:
                return await ctx.send(f"Couldn't find a channel matching `{target}`.")

        for role in roles:
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = None
            try:
                await channel.set_permissions(role, overwrite=overwrite)
            except discord.HTTPException:
                pass
        await ctx.send(f"🔓 Unlocked {channel.mention}.")

    @commands.hybrid_command(name="slowmode", description="Set slowmode delay for this channel (seconds).")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(seconds="Delay in seconds (0 to disable, max 21600)")
    async def slowmode(self, ctx: commands.Context, seconds: commands.Range[int, 0, 21600]):
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("Slowmode disabled.")
        else:
            await ctx.send(f"🐌 Slowmode set to {seconds}s.")

    # ---------- nuke ----------

    @commands.hybrid_command(name="nuke", description="Clone this channel (same permissions) and delete the original.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def nuke(self, ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        position = channel.position
        new_channel = await channel.clone(reason=f"Nuked by {ctx.author}")
        await new_channel.edit(position=position)
        await channel.delete(reason=f"Nuked by {ctx.author}")
        embed = discord.Embed(title="💥 Channel Nuked", description="This channel has been cleared.", color=discord.Color.red())
        await new_channel.send(embed=embed)

    @commands.hybrid_group(name="nukeschedule", invoke_without_command=True, description="Manage repeating scheduled channel nukes.")
    @commands.has_permissions(administrator=True)
    async def nukeschedule(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @nukeschedule.command(name="add", description="Schedule a channel to nuke on a repeating interval.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel to schedule nuking", interval="How often, e.g. 12h, 1d", message="Message posted after each nuke")
    async def nuke_add(self, ctx: commands.Context, channel: discord.TextChannel, interval: str, *, message: str = "This channel has been cleared."):
        try:
            delta = parse_duration(interval)
        except ValueError:
            return await ctx.send("Interval must end in s/m/h/d/w, e.g. 12h, 1d.")
        interval_minutes = max(1, int(delta.total_seconds() // 60))
        next_run = int(time.time() + delta.total_seconds())
        await db.add_nuke_schedule(ctx.guild.id, channel.id, interval_minutes, message, next_run)
        await ctx.send(
            f"💣 {channel.mention} will nuke every `{interval}`. "
            f"Next nuke: {discord.utils.format_dt(discord.utils.utcnow().fromtimestamp(next_run), 'R')}"
        )

    @nukeschedule.command(name="view", description="View the scheduled nuke message/timing for a channel.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel to check")
    async def nuke_view(self, ctx: commands.Context, channel: discord.TextChannel):
        row = await db.get_nuke_schedule(channel.id)
        if row is None:
            return await ctx.send(f"No scheduled nuke for {channel.mention}.")
        embed = discord.Embed(title=f"Scheduled Nuke — #{channel.name}", color=discord.Color.red())
        embed.add_field(name="Interval", value=f"Every {row['interval_minutes']} minutes", inline=False)
        embed.add_field(name="Message after nuke", value=row["message"], inline=False)
        embed.add_field(name="Next run", value=discord.utils.format_dt(discord.utils.utcnow().fromtimestamp(row["next_run"]), "R"), inline=False)
        await ctx.send(embed=embed)

    @nukeschedule.command(name="remove", description="Cancel a scheduled nuke for a channel.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel to stop nuking")
    async def nuke_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.remove_nuke_schedule(channel.id)
        await ctx.send(f"Cancelled the scheduled nuke for {channel.mention}.")

    @tasks.loop(minutes=1)
    async def check_nuke_schedule(self):
        due = await db.get_due_nuke_schedules(int(time.time()))
        for entry in due:
            guild = self.bot.get_guild(entry["guild_id"])
            channel = guild.get_channel(entry["channel_id"]) if guild else None
            if channel is None:
                await db.remove_nuke_schedule(entry["channel_id"])
                continue
            try:
                position = channel.position
                new_channel = await channel.clone(reason="Scheduled nuke")
                await new_channel.edit(position=position)
                await channel.delete(reason="Scheduled nuke")
                if entry["message"]:
                    await new_channel.send(entry["message"])
                # The channel gets a new ID after cloning, so re-key the schedule to it.
                await db.remove_nuke_schedule(entry["channel_id"])
                next_run = int(time.time()) + entry["interval_minutes"] * 60
                await db.add_nuke_schedule(entry["guild_id"], new_channel.id, entry["interval_minutes"], entry["message"], next_run)
            except discord.HTTPException:
                continue

    @check_nuke_schedule.before_loop
    async def before_check_nuke_schedule(self):
        await self.bot.wait_until_ready()

    # ---------- fix server ----------

    @commands.hybrid_group(name="fix", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def fix(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @fix.command(name="server", description="Disable threads, activities, application commands, and external apps across every channel.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    async def fix_server(self, ctx: commands.Context):
        count = 0
        for channel in ctx.guild.channels:
            if not isinstance(channel, (
                discord.TextChannel, discord.VoiceChannel, discord.StageChannel,
                discord.ForumChannel, discord.CategoryChannel,
            )):
                continue
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.create_public_threads = False
            overwrite.create_private_threads = False
            overwrite.send_messages_in_threads = False
            overwrite.use_application_commands = False
            overwrite.use_embedded_activities = False
            overwrite.use_external_apps = False
            try:
                await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
                count += 1
            except discord.HTTPException:
                pass
        await ctx.send(
            f"🔧 Server fixed — threads, activities, application commands, and external apps "
            f"disabled across {count} channel(s)."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
