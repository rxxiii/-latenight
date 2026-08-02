import time

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Blacklisted(commands.CheckFailure):
    """Raised when a blacklisted user tries to run a command, so
    on_command_error can give a specific message instead of the generic
    'no permission' one."""
    pass


async def not_blacklisted(ctx: commands.Context) -> bool:
    row = await db.blacklist_check(ctx.author.id)
    if row:
        raise Blacklisted(row["reason"] or "No reason given")
    return True


class Blacklist(commands.Cog):
    """,blacklist add/remove/list — blocks a user from using any bot command,
    anywhere. Restricted to server administrators of the server the command
    is run in (this is a bot-wide list, so use with care)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_check(not_blacklisted)

    def cog_unload(self):
        self.bot.remove_check(not_blacklisted)

    @commands.hybrid_group(name="blacklist", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def blacklist(self, ctx: commands.Context):
        await ctx.invoke(self.blacklist_list)

    @blacklist.command(name="add")
    @app_commands.describe(user="User to block from using the bot", reason="Optional reason")
    async def blacklist_add(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        if user.id == ctx.guild.owner_id or await self.bot.is_owner(user):
            return await ctx.send("Can't blacklist that user.")
        await db.blacklist_add(user.id, reason, int(time.time()))
        await ctx.send(f"🚫 {user.mention} can no longer use the bot. Reason: {reason}")

    @blacklist.command(name="remove")
    @app_commands.describe(user="User to unblock")
    async def blacklist_remove(self, ctx: commands.Context, user: discord.User):
        await db.blacklist_remove(user.id)
        await ctx.send(f"{user.mention} removed from the blacklist.")

    @blacklist.command(name="list")
    async def blacklist_list(self, ctx: commands.Context):
        rows = await db.blacklist_list()
        if not rows:
            return await ctx.send("No one is blacklisted.")
        lines = [f"<@{r['user_id']}> — {r['reason']}" for r in rows]
        await ctx.send("\n".join(lines)[:2000])


async def setup(bot: commands.Bot):
    await bot.add_cog(Blacklist(bot))
