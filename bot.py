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
