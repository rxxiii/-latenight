"""
Main entry point.

Run with:
    python bot.py

Requires a .env file with:
    DISCORD_TOKEN=your_token

Optional:
    DEFAULT_PREFIX=,
    DEV_GUILD_IDS=123456789012345678,987654321098765432
"""

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from database import db


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_PREFIX = os.getenv("DEFAULT_PREFIX", ",")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("bleedclone")


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()

intents.members = True
intents.message_content = True
intents.voice_states = True
intents.reactions = True
intents.presences = True


# ============================================================
# PREFIX
# ============================================================

async def get_prefix(bot: "BleedClone", message: discord.Message):
    """
    Get the guild-specific prefix from the existing database.

    The database/schema is NOT changed here.
    """

    if message.guild is None:
        return commands.when_mentioned_or(DEFAULT_PREFIX)(bot, message)

    try:
        row = await db.get_guild_config(message.guild.id)

        prefix = (
            row["prefix"]
            if row and row["prefix"]
            else DEFAULT_PREFIX
        )

    except Exception:
        # Do not let a database problem prevent commands from
        # being processed with the default prefix.
        log.exception(
            "Failed to retrieve prefix for guild %s. "
            "Falling back to default prefix.",
            message.guild.id,
        )
        prefix = DEFAULT_PREFIX

    return commands.when_mentioned_or(prefix)(bot, message)


# ============================================================
# BOT
# ============================================================

class BleedClone(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=commands.DefaultHelpCommand(),
        )

        self.failed_extensions: list[str] = []

    # --------------------------------------------------------
    # OWNER CHECK
    # --------------------------------------------------------

    async def is_owner(self, user: discord.abc.User) -> bool:
        """
        Treat configured co-owners as bot owners while preserving
        Discord.py's normal application owner check.
        """

        if await super().is_owner(user):
            return True

        try:
            return await db.coowner_check(user.id)
        except Exception:
            log.exception(
                "Co-owner check failed for user %s",
                user.id,
            )
            return False

    # --------------------------------------------------------
    # SAFE SEND
    # --------------------------------------------------------

    async def safe_ctx_send(
        self,
        ctx: commands.Context,
        content: str,
        **kwargs,
    ):
        """
        Send a prefix-command response without allowing a Discord
        permission/access error to create another exception.
        """

        try:
            return await ctx.send(content, **kwargs)

        except discord.Forbidden:
            log.warning(
                "Could not send a response in channel %s: "
                "missing access/permissions.",
                getattr(ctx.channel, "id", "unknown"),
            )

        except discord.NotFound:
            log.warning(
                "Could not send a response: channel/message "
                "was not found."
            )

        except discord.HTTPException as exc:
            log.warning(
                "Failed to send command response: %s",
                exc,
            )

        return None

    # --------------------------------------------------------
    # SETUP
    # --------------------------------------------------------

    async def setup_hook(self):

        # ----------------------------------------------------
        # DATABASE
        # ----------------------------------------------------

        try:
            await db.connect()
            log.info("Database connected.")
        except Exception:
            log.exception("Database connection failed.")
            raise

        # ----------------------------------------------------
        # EXTENSIONS
        # ----------------------------------------------------

        extensions = (
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
        )

        self.failed_extensions.clear()

        for extension in extensions:
            try:
                await self.load_extension(extension)
                log.info("Loaded extension %s", extension)

            except commands.ExtensionAlreadyLoaded:
                log.info("Extension %s was already loaded.", extension)

            except commands.ExtensionNotFound:
                self.failed_extensions.append(extension)
                log.error(
                    "Extension %s was not found.",
                    extension,
                )

            except Exception:
                self.failed_extensions.append(extension)
                log.exception(
                    "Failed to load extension %s",
                    extension,
                )

        # ----------------------------------------------------
        # PREFIX COMMAND CHECK
        # ----------------------------------------------------

        prefix_command = self.get_command("prefix")

        if prefix_command is None:
            log.error("PREFIX COMMAND FAILED TO REGISTER")

        elif isinstance(prefix_command, commands.Group):
            log.info(
                "Prefix command registered with subcommands: %s",
                ", ".join(
                    command.name
                    for command in prefix_command.commands
                ),
            )

        else:
            log.info("Prefix command registered.")

        # ----------------------------------------------------
        # GLOBAL SLASH COMMAND SYNC
        # ----------------------------------------------------

        try:
            synced = await self.tree.sync()

            log.info(
                "Synced %d slash commands globally.",
                len(synced),
            )

        except discord.HTTPException as exc:
            log.error(
                "Global slash-command sync failed: %s",
                exc,
            )

            if getattr(exc, "status", None) == 400:
                log.error(
                    "Discord rejected the global application-command "
                    "sync. This can happen when the bot has more than "
                    "Discord's allowed number of top-level global "
                    "application commands."
                )

        except Exception:
            log.exception(
                "Unexpected error while syncing global slash commands."
            )

        # ----------------------------------------------------
        # DEV GUILD SYNC
        # ----------------------------------------------------

        dev_guild_ids = os.getenv("DEV_GUILD_IDS", "")

        for raw_id in dev_guild_ids.split(","):

            raw_id = raw_id.strip()

            if not raw_id:
                continue

            try:
                guild_id = int(raw_id)

            except ValueError:
                log.error(
                    "Invalid guild ID in DEV_GUILD_IDS: %s",
                    raw_id,
                )
                continue

            # Check whether the bot actually knows this guild.
            guild = self.get_guild(guild_id)

            if guild is None:
                log.warning(
                    "Skipping dev guild sync for %s: "
                    "the bot is not currently in that guild "
                    "or Discord has not made it available.",
                    guild_id,
                )
                continue

            try:
                self.tree.copy_global_to(guild=guild)

                guild_synced = await self.tree.sync(
                    guild=guild
                )

                log.info(
                    "Synced %d slash commands to dev guild %s",
                    len(guild_synced),
                    guild_id,
                )

            except discord.Forbidden:
                log.warning(
                    "Cannot sync slash commands to guild %s: "
                    "bot has no access.",
                    guild_id,
                )

            except discord.NotFound:
                log.warning(
                    "Cannot sync slash commands to guild %s: "
                    "guild was not found.",
                    guild_id,
                )

            except discord.HTTPException as exc:
                log.error(
                    "Failed to sync slash commands to guild %s: %s",
                    guild_id,
                    exc,
                )

            except Exception:
                log.exception(
                    "Unexpected error syncing slash commands "
                    "to guild %s",
                    guild_id,
                )

        # ----------------------------------------------------
        # EXTENSION SUMMARY
        # ----------------------------------------------------

        if self.failed_extensions:
            log.warning(
                "Bot started with %d failed extension(s): %s",
                len(self.failed_extensions),
                ", ".join(self.failed_extensions),
            )
        else:
            log.info("All configured extensions loaded successfully.")

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    async def on_ready(self):

        log.info(
            "Logged in as %s (id: %s)",
            self.user,
            self.user.id,
        )

        try:
            await self.change_presence(
                activity=discord.Game(
                    name=f"{DEFAULT_PREFIX}help"
                )
            )
        except discord.HTTPException as exc:
            log.warning(
                "Failed to update bot presence: %s",
                exc,
            )

    # --------------------------------------------------------
    # MESSAGE EDIT
    # --------------------------------------------------------

    async def on_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ):

        if before.content == after.content:
            return

        # Allows:
        # ,timeotu
        #
        # to be edited into:
        # ,timeout
        #
        # and then processed.
        await self.process_commands(after)

    # --------------------------------------------------------
    # SLASH COMMAND ERRORS
    # --------------------------------------------------------

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):

        # Unwrap errors generated inside the command.
        original = getattr(error, "original", error)

        if isinstance(
            error,
            app_commands.MissingPermissions,
        ):
            message = (
                "You don't have permission to use this command."
            )

        elif isinstance(
            error,
            app_commands.BotMissingPermissions,
        ):
            message = (
                "I'm missing the Discord permissions required "
                "to do that."
            )

        elif isinstance(
            error,
            app_commands.CommandOnCooldown,
        ):
            message = (
                f"Try again in {error.retry_after:.1f}s."
            )

        elif isinstance(
            error,
            app_commands.CheckFailure,
        ):
            message = (
                "You don't have permission to use this command."
            )

        elif isinstance(original, discord.Forbidden):
            message = (
                "Discord denied that action. Check my role "
                "position and permissions."
            )

        elif isinstance(original, discord.NotFound):
            message = (
                "The requested Discord object could not be found."
            )

        else:
            log.error(
                "Slash command failed: %r",
                original,
                exc_info=(
                    type(original),
                    original,
                    original.__traceback__,
                ),
            )

            message = (
                "Something went wrong while running that command. "
                "Check the bot console for details."
            )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )

        except (
            discord.Forbidden,
            discord.NotFound,
            discord.HTTPException,
        ):
            log.warning(
                "Could not send slash-command error response."
            )

    # --------------------------------------------------------
    # PREFIX COMMAND ERRORS
    # --------------------------------------------------------

    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ):

        # Ignore unknown commands.
        if isinstance(error, commands.CommandNotFound):
            return

        # Unwrap errors raised inside a command.
        original = getattr(error, "original", error)

        # ----------------------------------------------------
        # CUSTOM BOT ERRORS
        # ----------------------------------------------------

        try:
            from cogs.blacklist import Blacklisted
        except (ImportError, AttributeError):
            Blacklisted = None

        if Blacklisted is not None and isinstance(
            error,
            Blacklisted,
        ):
            await self.safe_ctx_send(
                ctx,
                f"You're blacklisted from using this bot. "
                f"Reason: {error}",
            )
            return

        try:
            from cogs.lockdown import NotPermitted
        except (ImportError, AttributeError):
            NotPermitted = None

        if NotPermitted is not None and isinstance(
            error,
            NotPermitted,
        ):
            await self.safe_ctx_send(
                ctx,
                "This bot is currently restricted to its owner "
                "and permitted users.",
            )
            return

        # ----------------------------------------------------
        # PERMISSIONS
        # ----------------------------------------------------

        if isinstance(
            error,
            commands.MissingPermissions,
        ):
            await self.safe_ctx_send(
                ctx,
                "You don't have permission to use this command.",
            )
            return

        if isinstance(
            error,
            commands.BotMissingPermissions,
        ):
            missing = ", ".join(
                error.missing_permissions
            )

            await self.safe_ctx_send(
                ctx,
                f"I'm missing permissions to do that: `{missing}`",
            )
            return

        # ----------------------------------------------------
        # COMMON ARGUMENT ERRORS
        # ----------------------------------------------------

        if isinstance(error, commands.MemberNotFound):
            await self.safe_ctx_send(
                ctx,
                f"Couldn't find a member matching "
                f"`{error.argument}`.",
            )
            return

        if isinstance(error, commands.RoleNotFound):
            await self.safe_ctx_send(
                ctx,
                f"Couldn't find a role matching "
                f"`{error.argument}`.",
            )
            return

        if isinstance(error, commands.ChannelNotFound):
            await self.safe_ctx_send(
                ctx,
                f"Couldn't find a channel matching "
                f"`{error.argument}`.",
            )
            return

        if isinstance(
            error,
            commands.MissingRequiredArgument,
        ):
            command_name = (
                ctx.command.qualified_name
                if ctx.command
                else "this command"
            )

            await self.safe_ctx_send(
                ctx,
                f"Missing argument: `{error.param.name}`. "
                f"Check `,help {command_name}` for usage.",
            )
            return

        if isinstance(error, commands.BadArgument):
            await self.safe_ctx_send(
                ctx,
                f"Bad argument: {error}",
            )
            return

        # ----------------------------------------------------
        # DISCORD PERMISSION/ACCESS ERRORS
        # ----------------------------------------------------

        if isinstance(original, discord.Forbidden):
            await self.safe_ctx_send(
                ctx,
                "Discord won't let me do that — check my "
                "permissions and role position.",
            )
            return

        if isinstance(original, discord.NotFound):
            await self.safe_ctx_send(
                ctx,
                "The Discord object or message I was trying "
                "to use no longer exists.",
            )
            return

        # ----------------------------------------------------
        # RATE LIMIT / OTHER HTTP ERRORS
        # ----------------------------------------------------

        if isinstance(original, discord.HTTPException):
            log.error(
                "Discord HTTP error in command %s: %s",
                getattr(
                    ctx.command,
                    "qualified_name",
                    "unknown",
                ),
                original,
            )

            await self.safe_ctx_send(
                ctx,
                "Discord returned an error while running "
                "that command. Please try again.",
            )
            return

        # ----------------------------------------------------
        # UNKNOWN ERROR
        # ----------------------------------------------------

        log.error(
            "Unhandled command error in %s: %r",
            getattr(
                ctx.command,
                "qualified_name",
                "unknown",
            ),
            original,
            exc_info=(
                type(original),
                original,
                original.__traceback__,
            ),
        )

        await self.safe_ctx_send(
            ctx,
            f"Something went wrong running that command: "
            f"`{original}`",
        )


# ============================================================
# BOT INSTANCE
# ============================================================

bot = BleedClone()


# ============================================================
# MAIN
# ============================================================

async def main():

    if not TOKEN:
        raise SystemExit(
            "No DISCORD_TOKEN found. "
            "Copy .env.example to .env and fill in your bot token."
        )

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
