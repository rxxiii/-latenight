import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Aliases(commands.Cog):
    """Lets a server create custom aliases for existing commands, e.g.
    ,alias add b ban  ->  ,b @user spamming  runs the ban command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_group(name="alias", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def alias(self, ctx: commands.Context):
        await ctx.invoke(self.alias_list)

    @alias.command(name="add")
    @app_commands.describe(alias_name="The new alias word", command="The existing command it should run, e.g. ban")
    async def alias_add(self, ctx: commands.Context, alias_name: str, *, command: str):
        real_command = self.bot.get_command(command.split()[0])
        if real_command is None:
            return await ctx.send(f"`{command}` isn't a real command.")
        await db.add_alias(ctx.guild.id, alias_name, command)
        await ctx.send(f"Alias `{alias_name}` now runs `{command}`.")

    @alias.command(name="remove")
    @app_commands.describe(alias_name="The alias to remove")
    async def alias_remove(self, ctx: commands.Context, alias_name: str):
        await db.remove_alias(ctx.guild.id, alias_name)
        await ctx.send(f"Alias `{alias_name}` removed.")

    @alias.command(name="view")
    @app_commands.describe(alias_name="The alias to look up")
    async def alias_view(self, ctx: commands.Context, alias_name: str):
        mapped = await db.get_alias(ctx.guild.id, alias_name)
        if mapped is None:
            return await ctx.send(f"No alias called `{alias_name}`.")
        await ctx.send(f"`{alias_name}` runs `{mapped}`.")

    @alias.command(name="list")
    async def alias_list(self, ctx: commands.Context):
        rows = await db.list_aliases(ctx.guild.id)
        if not rows:
            return await ctx.send("No aliases configured.")
        lines = [f"`{r['alias']}` → `{r['command']}`" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        row = await db.get_guild_config(message.guild.id)
        prefix = row["prefix"]
        if not message.content.startswith(prefix):
            return

        rest = message.content[len(prefix):]
        parts = rest.split(maxsplit=1)
        if not parts:
            return

        alias_word = parts[0].lower()
        # Don't fight real commands — only rewrite if this word isn't already one.
        if self.bot.get_command(alias_word) is not None:
            return

        mapped_command = await db.get_alias(message.guild.id, alias_word)
        if mapped_command is None:
            return

        new_content = f"{prefix}{mapped_command}" + (f" {parts[1]}" if len(parts) > 1 else "")
        message.content = new_content
        ctx = await self.bot.get_context(message)
        await self.bot.invoke(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(Aliases(bot))
