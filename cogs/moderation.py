import datetime
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db
from cogs.fakeperms import fake_or_real_permission



async def _check_bot_can_ban(ctx, member: discord.Member) -> bool:
    me = ctx.guild.me
    if me is None:
        await ctx.send("❌ I couldn't determine my permissions in this server.")
        return False
    if not me.guild_permissions.ban_members:
        await ctx.send("❌ I need the **Ban Members** permission.")
        return False
    if member.id == ctx.guild.owner_id:
        await ctx.send("❌ I can't ban the server owner.")
        return False
    if member.id == me.id:
        await ctx.send("❌ I can't ban myself.")
        return False
    if member.top_role >= me.top_role:
        await ctx.send(
            f"❌ I can't ban {member.mention}. My highest role "
            f"({me.top_role.mention}) must be **above** their highest role "
            f"({member.top_role.mention})."
        )
        return False
    return True


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


def _human_duration(delta: datetime.timedelta) -> str:
    total = int(delta.total_seconds())
    for name, size in (
        ("week", 604800), ("day", 86400), ("hour", 3600),
        ("minute", 60), ("second", 1),
    ):
        if total >= size and total % size == 0:
            value = total // size
            return f"{value} {name}{'' if value == 1 else 's'}"
    return str(delta)


def _no_pings():
    # Keeps mentions visually formatted without notifying the user/role.
    return discord.AllowedMentions.none()


async def _bleed_embed(ctx: commands.Context, text: str, color: discord.Color):
    embed = discord.Embed(description=text, color=color)
    await ctx.send(embed=embed, allowed_mentions=_no_pings())


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
        await _bleed_embed(ctx, f"🔨 {ctx.author.mention}: Banned **{member.display_name}** — {reason}", discord.Color.red())

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
        await _bleed_embed(ctx, f"👢 {ctx.author.mention}: Kicked **{member.display_name}** — {reason}", discord.Color.red())

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
        await _bleed_embed(ctx, f"🔇 {ctx.author.mention}: {member.display_name} is now timed out for **{_human_duration(delta)}** — {reason}", discord.Color.red())

    @commands.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove a member's timeout.")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    @app_commands.describe(member="Member to remove timeout from", reason="Reason")
    async def untimeout_cmd(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        await member.timeout(None, reason=f"{ctx.author}: {reason}")
        await _bleed_embed(ctx, f"🔊 {ctx.author.mention}: {member.display_name} is no longer timed out" + (f" — {reason}" if reason != "No reason provided" else ""), discord.Color.red())

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
        await ctx.send(f"⚠️ Warned **{member.display_name}** — {reason}", allowed_mentions=_no_pings())
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
        # ,fn @member (or ,forcenickname @member) locks their CURRENT nickname.
        # Use the literal word "clear" to remove an existing force-nickname.
        if new_nickname and new_nickname.strip().lower() == "clear":
            await db.remove_forced_nickname(ctx.guild.id, member.id)
            return await ctx.send(f"🔓 {member.mention}\'s nickname is no longer locked.")

        if not new_nickname:
            new_nickname = member.nick or member.name

        await member.edit(nick=new_nickname, reason=f"Force-nicknamed by {ctx.author}")
        await db.set_forced_nickname(ctx.guild.id, member.id, new_nickname)
        await ctx.send(f"🔒 {member.mention}\'s nickname is locked to **{new_nickname}**.")

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

    async def _lock_roles(self, guild: discord.Guild):
        staff_role_ids = set(await db.list_staff_roles(guild.id))
        staff_roles = [r for r in guild.roles if not r.managed and r.id in staff_role_ids]
        bot_role = guild.me.top_role if guild.me else None
        locked_roles = [
            r for r in guild.roles
            if not r.managed and r != guild.default_role and r.id not in staff_role_ids and r != bot_role
        ]
        return locked_roles, staff_roles, bot_role

    async def _apply_lock(self, channel: discord.TextChannel):
        locked_roles, staff_roles, bot_role = await self._lock_roles(channel.guild)

        # Deny the base permission and every non-staff role explicitly. This
        # prevents a role with a pre-existing allow from bypassing the lock.
        everyone = channel.overwrites_for(channel.guild.default_role)
        everyone.send_messages = False
        await channel.set_permissions(channel.guild.default_role, overwrite=everyone)

        for role in locked_roles:
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = False
            try:
                await channel.set_permissions(role, overwrite=overwrite)
            except discord.HTTPException:
                pass

        # Explicitly allow staff roles. Staff members also receive a member
        # overwrite so a staff member who happens to have a normal/non-staff
        # role is not blocked by that role's deny.
        for role in staff_roles:
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = True
            try:
                await channel.set_permissions(role, overwrite=overwrite)
            except discord.HTTPException:
                pass

        if bot_role and bot_role != channel.guild.default_role:
            overwrite = channel.overwrites_for(bot_role)
            overwrite.send_messages = True
            try:
                await channel.set_permissions(bot_role, overwrite=overwrite)
            except discord.HTTPException:
                pass

        staff_role_ids = {r.id for r in staff_roles}
        for member in channel.guild.members:
            if member.bot and member.id == self.bot.user.id:
                should_allow = True
            else:
                should_allow = any(r.id in staff_role_ids for r in member.roles)
            if not should_allow:
                continue
            try:
                overwrite = channel.overwrites_for(member)
                overwrite.send_messages = True
                await channel.set_permissions(member, overwrite=overwrite)
            except discord.HTTPException:
                pass

    async def _apply_unlock(self, channel: discord.TextChannel):
        locked_roles, staff_roles, bot_role = await self._lock_roles(channel.guild)
        roles = [channel.guild.default_role, *locked_roles, *staff_roles]
        if bot_role and bot_role != channel.guild.default_role:
            roles.append(bot_role)
        seen = set()
        for role in roles:
            if role.id in seen:
                continue
            seen.add(role.id)
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = None
            try:
                await channel.set_permissions(role, overwrite=overwrite)
            except discord.HTTPException:
                pass

        staff_role_ids = {r.id for r in staff_roles}
        for member in channel.guild.members:
            if member.bot and member.id == self.bot.user.id:
                should_clear = True
            else:
                should_clear = any(r.id in staff_role_ids for r in member.roles)
            if not should_clear:
                continue
            try:
                overwrite = channel.overwrites_for(member)
                overwrite.send_messages = None
                await channel.set_permissions(member, overwrite=overwrite)
            except discord.HTTPException:
                pass

    @commands.hybrid_command(
        name="lock",
        description="Lock the current channel, or use 'all' to lock every text channel. Staff roles stay able to type.",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(target="Use 'all' for every text channel, or leave blank for this channel")
    async def lock(self, ctx: commands.Context, target: str = None):
        if target and target.lower() == "all":
            count = 0
            for channel in ctx.guild.text_channels:
                try:
                    await self._apply_lock(channel)
                    count += 1
                except discord.HTTPException:
                    pass
            return await ctx.send(f"🔒 Locked {count} text channel(s). Every non-staff role is blocked from typing; bound staff roles remain allowed.")

        channel = ctx.channel
        if target:
            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
            except commands.BadArgument:
                return await ctx.send(f"Couldn't find a channel matching `{target}`.")
        await self._apply_lock(channel)
        await ctx.send(f"🔒 Locked {channel.mention}. Every non-staff role is blocked from typing; bound staff roles remain allowed.")

    @commands.hybrid_command(
        name="unlock",
        description="Unlock the current channel, or use 'all' to unlock every text channel.",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(target="Use 'all' for every text channel, or leave blank for this channel")
    async def unlock(self, ctx: commands.Context, target: str = None):
        if target and target.lower() == "all":
            count = 0
            for channel in ctx.guild.text_channels:
                try:
                    await self._apply_unlock(channel)
                    count += 1
                except discord.HTTPException:
                    pass
            return await ctx.send(f"🔓 Unlocked {count} text channel(s).")

        channel = ctx.channel
        if target:
            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
            except commands.BadArgument:
                return await ctx.send(f"Couldn't find a channel matching `{target}`.")
        await self._apply_unlock(channel)
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

    # ---------- nuke / scheduled nuke ----------

    async def _execute_nuke(self, channel: discord.TextChannel, reason: str, message: str | None = None):
        position = channel.position
        new_channel = await channel.clone(reason=reason)
        await new_channel.edit(position=position)
        await channel.delete(reason=reason)
        if message:
            await new_channel.send(message)
        else:
            embed = discord.Embed(
                title="💥 Channel Nuked",
                description="This channel has been cleared.",
                color=discord.Color.red(),
            )
            await new_channel.send(embed=embed)
        return new_channel

    @commands.hybrid_group(
        name="nuke",
        invoke_without_command=True,
        description="Nuke this channel, or manage scheduled nukes with add/view/remove.",
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def nuke(self, ctx: commands.Context):
        """Prefix: ,nuke immediately clones and replaces the current channel.
        Slash users can use /nuke add, /nuke view, and /nuke remove."""
        if ctx.interaction:
            return await ctx.send_help(ctx.command)
        try:
            await self._execute_nuke(ctx.channel, f"Nuked by {ctx.author}")
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't nuke this channel: {e}")

    @nuke.command(name="add", description="Schedule a channel to be nuked repeatedly.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    @app_commands.describe(
        channel="Channel to schedule nuking",
        interval="How often, e.g. 12h, 1d",
        message="Message posted after each scheduled nuke",
    )
    async def nuke_add(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        interval: str,
        *,
        message: str = "This channel has been cleared.",
    ):
        try:
            delta = parse_duration(interval)
        except ValueError:
            return await ctx.send("Interval must end in s/m/h/d/w, e.g. 12h, 1d.")
        if delta.total_seconds() < 60:
            return await ctx.send("Scheduled nukes must be at least 1 minute apart.")
        interval_minutes = max(1, int(delta.total_seconds() // 60))
        next_run = int(time.time() + delta.total_seconds())
        await db.add_nuke_schedule(ctx.guild.id, channel.id, interval_minutes, message, next_run)
        await ctx.send(
            f"💣 {channel.mention} will nuke every `{interval}`. "
            f"Next nuke: {discord.utils.format_dt(discord.utils.utcnow().fromtimestamp(next_run), 'R')}"
        )

    @nuke.command(name="view", description="View the scheduled nuke for a channel.")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel to check")
    async def nuke_view(self, ctx: commands.Context, channel: discord.TextChannel):
        row = await db.get_nuke_schedule(channel.id)
        if row is None:
            return await ctx.send(f"No scheduled nuke for {channel.mention}.")
        embed = discord.Embed(title=f"Scheduled Nuke — #{channel.name}", color=discord.Color.red())
        embed.add_field(name="Interval", value=f"Every {row['interval_minutes']} minutes", inline=False)
        embed.add_field(name="Message after nuke", value=row["message"], inline=False)
        embed.add_field(
            name="Next run",
            value=discord.utils.format_dt(discord.utils.utcnow().fromtimestamp(row["next_run"]), "R"),
            inline=False,
        )
        await ctx.send(embed=embed)

    @nuke.command(name="remove", description="Cancel the scheduled nuke for a channel.")
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
                new_channel = await self._execute_nuke(
                    channel,
                    "Scheduled nuke",
                    entry["message"],
                )
                await db.remove_nuke_schedule(entry["channel_id"])
                next_run = int(time.time()) + entry["interval_minutes"] * 60
                await db.add_nuke_schedule(
                    entry["guild_id"],
                    new_channel.id,
                    entry["interval_minutes"],
                    entry["message"],
                    next_run,
                )
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
