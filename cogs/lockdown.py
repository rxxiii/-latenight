import time

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class NotPermitted(commands.CheckFailure):
    """Raised when someone who isn't the bot owner, co-owner, or explicitly permitted
    tries to use any command."""
    pass


async def owner_or_permitted(ctx: commands.Context) -> bool:
    if await ctx.bot.is_owner(ctx.author):
        return True
    if await db.permit_check(ctx.author.id):
        return True
    raise NotPermitted("Only the bot owner and permitted users can use this bot.")


class Lockdown(commands.Cog):
    """Restricts every command to the bot owner, co-owners, plus anyone the owner has
    explicitly permitted with ,permit add."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_check(owner_or_permitted)

    def cog_unload(self):
        self.bot.remove_check(owner_or_permitted)

    @commands.hybrid_group(name="permit", invoke_without_command=True)
    @commands.is_owner()
    async def permit(self, ctx: commands.Context):
        await ctx.invoke(self.permit_list)

    @permit.command(name="add")
    @commands.is_owner()
    @app_commands.describe(user="User to allow using the bot")
    async def permit_add(self, ctx: commands.Context, user: discord.User):
        await db.permit_add(user.id, int(time.time()))
        await ctx.send(f"✅ {user.mention} can now use the bot.")

    @permit.command(name="remove")
    @commands.is_owner()
    @app_commands.describe(user="User to revoke access from")
    async def permit_remove(self, ctx: commands.Context, user: discord.User):
        await db.permit_remove(user.id)
        await ctx.send(f"{user.mention} can no longer use the bot.")

    @permit.command(name="list")
    @commands.is_owner()
    async def permit_list(self, ctx: commands.Context):
        rows = await db.permit_list()
        if not rows:
            return await ctx.send("No one is permitted yet — only you (the bot owner) can use the bot.")
        lines = [f"<@{r['user_id']}>" for r in rows]
        await ctx.send("Permitted users:\n" + "\n".join(lines))


    @commands.hybrid_group(name="coowner", invoke_without_command=True)
    @commands.is_owner()
    async def coowner(self, ctx: commands.Context):
        """Manage bot co-owners. Co-owners have the same owner-level access."""
        await ctx.invoke(self.coowner_list)

    @coowner.command(name="add")
    @commands.is_owner()
    @app_commands.describe(user="User to give full bot-owner access")
    async def coowner_add(self, ctx: commands.Context, user: discord.User):
        if await self.bot.is_owner(user):
            # This also covers the real application owner.
            return await ctx.send(f"ℹ️ {user.mention} already has bot-owner access.")
        await db.coowner_add(user.id, int(time.time()))
        await ctx.send(f"✅ {user.mention} is now a bot co-owner and has full owner-level access.")

    @coowner.command(name="remove")
    @commands.is_owner()
    @app_commands.describe(user="User to remove from bot co-owner access")
    async def coowner_remove(self, ctx: commands.Context, user: discord.User):
        if await commands.Bot.is_owner(self.bot, user):
            return await ctx.send("❌ You can't remove the actual Discord application owner.")
        await db.coowner_remove(user.id)
        await ctx.send(f"✅ {user.mention} is no longer a bot co-owner.")

    @coowner.command(name="list")
    @commands.is_owner()
    async def coowner_list(self, ctx: commands.Context):
        rows = await db.coowner_list()
        if not rows:
            return await ctx.send("No bot co-owners are configured.")
        lines = [f"<@{r['user_id']}>" for r in rows]
        await ctx.send("**Bot co-owners:**\n" + "\n".join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(Lockdown(bot))
