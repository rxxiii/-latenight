import datetime
import time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class AntiRaid(commands.Cog):
    """Detects mass-join raids and can auto-kick accounts that are too new.
    Also supports a manual raid lockdown mode."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> deque of join timestamps, used to detect a burst of joins
        self.recent_joins: dict[int, deque] = defaultdict(deque)

    @commands.hybrid_group(name="antiraid", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antiraid(self, ctx: commands.Context):
        await ctx.invoke(self.antiraid_config)

    @antiraid.command(name="config")
    @commands.has_permissions(administrator=True)
    async def antiraid_config(self, ctx: commands.Context):
        config = await db.get_antiraid_config(ctx.guild.id)
        embed = discord.Embed(title="Antiraid Configuration", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value="✅" if config["enabled"] else "❌")
        embed.add_field(name="Raid mode", value="🔴 ACTIVE" if config["raid_mode"] else "🟢 inactive")
        embed.add_field(name="Mass-join trigger", value=f"{config['massjoin_count']} joins / {config['massjoin_seconds']}s")
        embed.add_field(name="Min account age", value=f"{config['min_account_age_days']} days")
        await ctx.send(embed=embed)

    @antiraid.command(name="state")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on or off")
    async def antiraid_state(self, ctx: commands.Context, state: str):
        await db.set_antiraid_config(ctx.guild.id, enabled=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Antiraid: **{state}**")

    @antiraid.command(name="massjoin")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(count="Number of joins", seconds="Time window in seconds")
    async def antiraid_massjoin(self, ctx: commands.Context, count: app_commands.Range[int, 2, 100], seconds: app_commands.Range[int, 2, 300]):
        await db.set_antiraid_config(ctx.guild.id, massjoin_count=count, massjoin_seconds=seconds)
        await ctx.send(f"Mass-join trigger set to **{count} joins / {seconds}s**.")

    @antiraid.command(name="age")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(days="Minimum account age in days required to join (0 to disable)")
    async def antiraid_age(self, ctx: commands.Context, days: app_commands.Range[int, 0, 365]):
        await db.set_antiraid_config(ctx.guild.id, min_account_age_days=days)
        await ctx.send(f"Minimum account age set to **{days} days**.")

    @antiraid.command(name="raid")
    @commands.has_permissions(administrator=True)
    @app_commands.describe(state="on to lock the server down manually, off to lift it")
    async def antiraid_raid(self, ctx: commands.Context, state: str):
        turning_on = state.lower() in ("on", "enable", "true")
        await db.set_antiraid_config(ctx.guild.id, raid_mode=1 if turning_on else 0)
        if turning_on:
            await ctx.send("🚨 Raid mode **ACTIVE** — new joins will be kicked until turned off.")
        else:
            await ctx.send("Raid mode lifted.")

    @antiraid.group(name="whitelist", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antiraid_whitelist(self, ctx: commands.Context):
        await ctx.invoke(self.antiraid_whitelist_view)

    @antiraid_whitelist.command(name="add")
    @app_commands.describe(member="Member to exempt from antiraid checks")
    async def antiraid_whitelist_add(self, ctx: commands.Context, member: discord.Member):
        await db.antiraid_whitelist_add(ctx.guild.id, member.id)
        await ctx.send(f"{member.mention} is now whitelisted from antiraid.")

    @antiraid_whitelist.command(name="remove")
    @app_commands.describe(member="Member to remove from the whitelist")
    async def antiraid_whitelist_remove(self, ctx: commands.Context, member: discord.Member):
        await db.antiraid_whitelist_remove(ctx.guild.id, member.id)
        await ctx.send(f"{member.mention} removed from the antiraid whitelist.")

    @antiraid_whitelist.command(name="view")
    async def antiraid_whitelist_view(self, ctx: commands.Context):
        ids = await db.antiraid_whitelist_list(ctx.guild.id)
        if not ids:
            return await ctx.send("No one is whitelisted.")
        await ctx.send("\n".join(f"<@{i}>" for i in ids))

    @commands.hybrid_command(name="recentban", description="Show the most recent bans in this server.")
    @commands.has_permissions(ban_members=True)
    @app_commands.describe(count="How many recent bans to show (max 25)")
    async def recentban(self, ctx: commands.Context, count: app_commands.Range[int, 1, 25] = 10):
        entries = []
        async for entry in ctx.guild.audit_logs(action=discord.AuditLogAction.ban, limit=count):
            entries.append(f"**{entry.target}** — banned by {entry.user} ({discord.utils.format_dt(entry.created_at, 'R')})")
        if not entries:
            return await ctx.send("No recent bans found.")
        await ctx.send("\n".join(entries)[:2000])

    # ---------- join handling ----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        config = await db.get_antiraid_config(guild.id)
        if not config["enabled"]:
            return
        if await db.antiraid_is_whitelisted(guild.id, member.id):
            return

        # Manual raid mode: kick everyone who joins while it's active.
        if config["raid_mode"]:
            try:
                await member.kick(reason="Antiraid: raid mode active")
            except discord.HTTPException:
                pass
            return

        # Minimum account age check.
        if config["min_account_age_days"] > 0:
            age = datetime.datetime.now(datetime.timezone.utc) - member.created_at
            if age.days < config["min_account_age_days"]:
                try:
                    await member.kick(reason=f"Antiraid: account younger than {config['min_account_age_days']} days")
                except discord.HTTPException:
                    pass
                return

        # Mass-join burst detection.
        now = time.time()
        window = config["massjoin_seconds"]
        joins = self.recent_joins[guild.id]
        joins.append(now)
        while joins and now - joins[0] > window:
            joins.popleft()

        if len(joins) >= config["massjoin_count"]:
            # Trip raid mode automatically.
            await db.set_antiraid_config(guild.id, raid_mode=1)
            if config["log_channel_id"]:
                channel = guild.get_channel(config["log_channel_id"])
                if channel:
                    await channel.send(
                        f"🚨 **Raid detected** — {len(joins)} joins in {window}s. Raid mode auto-enabled. "
                        f"Use `,antiraid raid off` once it's safe."
                    )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
