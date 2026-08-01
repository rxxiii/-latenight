import discord
from discord import app_commands
from discord.ext import commands

from database import db


def render(template: str, member: discord.Member) -> str:
    return (
        template.replace("{user}", member.mention)
        .replace("{username}", member.display_name)
        .replace("{server}", member.guild.name)
        .replace("{membercount}", str(member.guild.member_count))
    )


class Welcome(commands.Cog):
    """Welcome/goodbye messages and simple trigger -> response auto-responders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- welcome / goodbye setup ----------

    @commands.hybrid_group(name="welcome", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def welcome(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @welcome.command(name="channel")
    @app_commands.describe(channel="Channel to send welcome messages in")
    async def welcome_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.set_guild_config(ctx.guild.id, welcome_channel_id=channel.id)
        await ctx.send(f"Welcome messages will be sent in {channel.mention}.")

    @welcome.command(name="message")
    @app_commands.describe(message="Use {user}, {username}, {server}, {membercount}")
    async def welcome_message(self, ctx: commands.Context, *, message: str):
        await db.set_guild_config(ctx.guild.id, welcome_message=message)
        await ctx.send("Welcome message updated.")

    @welcome.command(name="test")
    async def welcome_test(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)
        if not row["welcome_message"]:
            return await ctx.send("No welcome message configured yet.")
        await ctx.send(render(row["welcome_message"], ctx.author))

    @commands.hybrid_group(name="goodbye", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def goodbye(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @goodbye.command(name="channel")
    @app_commands.describe(channel="Channel to send goodbye messages in")
    async def goodbye_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.set_guild_config(ctx.guild.id, goodbye_channel_id=channel.id)
        await ctx.send(f"Goodbye messages will be sent in {channel.mention}.")

    @goodbye.command(name="message")
    @app_commands.describe(message="Use {user}, {username}, {server}, {membercount}")
    async def goodbye_message(self, ctx: commands.Context, *, message: str):
        await db.set_guild_config(ctx.guild.id, goodbye_message=message)
        await ctx.send("Goodbye message updated.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        row = await db.get_guild_config(member.guild.id)
        if row["welcome_channel_id"] and row["welcome_message"]:
            channel = member.guild.get_channel(row["welcome_channel_id"])
            if channel:
                await channel.send(render(row["welcome_message"], member))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        row = await db.get_guild_config(member.guild.id)
        if row["goodbye_channel_id"] and row["goodbye_message"]:
            channel = member.guild.get_channel(row["goodbye_channel_id"])
            if channel:
                await channel.send(render(row["goodbye_message"], member))

    # ---------- auto responders ----------

    @commands.hybrid_group(name="autoresponder", aliases=["ar"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def autoresponder(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @autoresponder.command(name="add")
    @app_commands.describe(
        trigger="Word/phrase that triggers the response",
        response="What the bot replies with",
        exact="If true, message must exactly match the trigger (default: contains)",
    )
    async def autoresponder_add(self, ctx: commands.Context, trigger: str, response: str, exact: bool = False):
        await db.add_autoresponder(ctx.guild.id, trigger, response, exact)
        await ctx.send(f"Added auto-responder: `{trigger}` → {response}")

    @autoresponder.command(name="remove")
    @app_commands.describe(trigger="The trigger to remove")
    async def autoresponder_remove(self, ctx: commands.Context, trigger: str):
        await db.remove_autoresponder(ctx.guild.id, trigger)
        await ctx.send(f"Removed auto-responder for `{trigger}`.")

    @autoresponder.command(name="list")
    async def autoresponder_list(self, ctx: commands.Context):
        rows = await db.get_autoresponders(ctx.guild.id)
        if not rows:
            return await ctx.send("No auto-responders configured.")
        lines = [f"`{r['trigger']}` → {r['response']}" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @commands.Cog.listener()
