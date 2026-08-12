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
from discord import app_commands
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

    async def is_owner(self, user: discord.abc.User) -> bool:
        """Treat configured bot co-owners as bot owners for every owner-only command.

        This intentionally wraps the normal Discord.py owner check, so the
        application owner keeps full control and co-owners receive the same
        owner checks throughout all cogs.
        """
        if await super().is_owner(user):
            return True
        return await db.coowner_check(user.id)

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
            "cogs.blacklist",
            "cogs.content_filter",
            "cogs.lockdown",
            "cogs.games",
            "cogs.music",
            "cogs.osint",
            "cogs.core",
        ):
            try:
                await self.load_extension(extension)
                log.info("Loaded extension %s", extension)
            except Exception:
                log.exception("Failed to load extension %s", extension)

        prefix_command = self.get_command("prefix")
        if prefix_command is None:
            log.error("PREFIX COMMAND FAILED TO REGISTER")
        elif isinstance(prefix_command, commands.Group):
            log.info("Prefix command registered with subcommands: %s", ", ".join(c.name for c in prefix_command.commands))
        else:
            log.info("Prefix command registered.")

        # Register application commands globally so they are
        # available in every server where the bot is installed.
        # DEV_GUILD_ID is intentionally not used for slash-command syncing.
        synced = await self.tree.sync()
        log.info("Synced %d slash commands globally.", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name=f"{DEFAULT_PREFIX}help"))

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Lets people fix a typo'd command (,timeotu -> ,timeout) by editing
        # the message instead of retyping it.
        if before.content == after.content:
            return
        await self.process_commands(after)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Make slash-command failures visible instead of looking like missing commands."""
        if isinstance(error, app_commands.MissingPermissions):
            message = "You don't have permission to use this command."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "I'm missing the Discord permissions required to do that."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Try again in {error.retry_after:.1f}s."
        elif isinstance(error, app_commands.CheckFailure):
            message = "You don't have permission to use this command."
        else:
            log.exception("Slash command failed", exc_info=error)
            message = "Something went wrong while running that command. Check the bot console for details."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        # Without this, a failed permission check or bad argument fails
        # completely silently â the user just sees nothing happen.
        if isinstance(error, commands.CommandNotFound):
            return
        from cogs.blacklist import Blacklisted
        if isinstance(error, Blacklisted):
            return await ctx.send(f"You're blacklisted from using this bot. Reason: {error}")
        from cogs.lockdown import NotPermitted
        if isinstance(error, NotPermitted):
            return await ctx.send("This bot is currently restricted to its owner and permitted users.")
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
            return await ctx.send("Discord won't let me do that â check my role position and permissions.")

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
