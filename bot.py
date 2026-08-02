"""
Main entry point.

Run with:  python bot.py
Requires a .env file (copy .env.example) with your bot token.
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_PREFIX = os.getenv("DEFAULT_PREFIX", ",")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bleedclone")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.reactions = True
intents.presences = True  # needed for vanity role status detection


async def get_prefix(bot: "BleedClone", message: discord.Message):
    if message.guild is None:
        return commands.when_mentioned_or(DEFAULT_PREFIX)(bot, message)
    row = await db.get_guild_config(message.guild.id)
    prefix = row["prefix"] if row and row["prefix"] else DEFAULT_PREFIX
    return commands.when_mentioned_or(prefix)(bot, message)


class BleedClone(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=commands.DefaultHelpCommand(),
        )

    async def setup_hook(self):
        await db.connect()
        log.info("Database connected.")

        for extension in (
            "cogs.moderation",
            "cogs.moderation_extended",
            "cogs.welcome",
            "cogs.roles",
            "cogs.tickets",
            "cogs.giveaways",
            "cogs.voicemaster",
            "cogs.antinuke",
            "cogs.antiraid",
            "cogs.filter",
            "cogs.aliases",
            "cogs.utility",
            "cogs.boosters",
            "cogs.logging_events",
            "cogs.fakeperms",
            "cogs.snipe",
            "cogs.core",
        ):
            try:
                await self.load_extension(extension)
                log.info("Loaded extension %s", extension)
            except Exception:
                log.exception("Failed to load extension %s", extension)

        # Sync slash commands globally. During development, syncing to a
        # single guild (via GUILD_ID env var) is much faster than a global
        # sync, which can take up to an hour to propagate.
        guild_id = os.getenv("DEV_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced slash commands to dev guild %s", guild_id)
        else:
            await self.tree.sync()
            log.info("Synced slash commands globally.")

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name=f"{DEFAULT_PREFIX}help"))

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Lets people fix a typo'd command (,timeotu -> ,timeout) by editing
        # the message instead of retyping it.
        if before.content == after.content:
            return
        await self.process_commands(after)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        # Without this, a failed permission check or bad argument fails
        # completely silently — the user just sees nothing happen.
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
            return await ctx.send("You don't have permission to use this command.")
        if isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            return await ctx.send(f"I'm missing permissions to do that: `{missing}`")
        if isinstance(error, commands.MemberNotFound):
            return await ctx.send(f"Couldn't find a member matching `{error.argument}`.")
        if isinstance(error, commands.RoleNotFound):
            return await ctx.send(f"Couldn't find a role matching `{error.argument}`.")
        if isinstance(error, commands.ChannelNotFound):
            return await ctx.send(f"Couldn't find a channel matching `{error.argument}`.")
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(f"Missing argument: `{error.param.name}`. Check `,help {ctx.command}` for usage.")
        if isinstance(error, commands.BadArgument):
            return await ctx.send(f"Bad argument: {error}")
        if isinstance(error, discord.Forbidden):
            return await ctx.send("Discord won't let me do that — check my role position and permissions.")

        log.exception("Unhandled command error in %s", ctx.command, exc_info=error)
        await ctx.send(f"Something went wrong running that command: `{error}`")


bot = BleedClone()


async def main():
    if not TOKEN:
        raise SystemExit(
            "No DISCORD_TOKEN found. Copy .env.example to .env and fill in your bot token."
        )
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
