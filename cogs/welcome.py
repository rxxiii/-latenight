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

    @welcome.command(name="add")
    @app_commands.describe(channel="Channel to send this welcome message in", message="Use {user}, {username}, {server}, {membercount}")
    async def welcome_add(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        new_id = await db.add_event_message("welcome_messages", ctx.guild.id, channel.id, message)
        await ctx.send(f"Added welcome message `#{new_id}` for {channel.mention}.")

    @welcome.command(name="remove")
    @app_commands.describe(message_id="ID shown in ,welcome list")
    async def welcome_remove(self, ctx: commands.Context, message_id: int):
        await db.remove_event_message("welcome_messages", message_id)
        await ctx.send(f"Removed welcome message `#{message_id}`.")

    @welcome.command(name="view")
    @app_commands.describe(message_id="ID shown in ,welcome list")
    async def welcome_view(self, ctx: commands.Context, message_id: int):
        row = await db.get_event_message("welcome_messages", message_id)
        if row is None:
            return await ctx.send("No welcome message with that ID.")
        await ctx.send(f"**Channel:** <#{row['channel_id']}>\n**Message:** {row['message']}")

    @welcome.command(name="list")
    async def welcome_list(self, ctx: commands.Context):
        rows = await db.list_event_messages("welcome_messages", ctx.guild.id)
        if not rows:
            return await ctx.send("No welcome messages configured.")
        lines = [f"`#{r['id']}` → <#{r['channel_id']}>: {r['message'][:80]}" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

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

    @goodbye.command(name="add")
    @app_commands.describe(channel="Channel to send this goodbye message in", message="Use {user}, {username}, {server}, {membercount}")
    async def goodbye_add(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        new_id = await db.add_event_message("goodbye_messages", ctx.guild.id, channel.id, message)
        await ctx.send(f"Added goodbye message `#{new_id}` for {channel.mention}.")

    @goodbye.command(name="remove")
    @app_commands.describe(message_id="ID shown in ,goodbye list")
    async def goodbye_remove(self, ctx: commands.Context, message_id: int):
        await db.remove_event_message("goodbye_messages", message_id)
        await ctx.send(f"Removed goodbye message `#{message_id}`.")

    @goodbye.command(name="view")
    @app_commands.describe(message_id="ID shown in ,goodbye list")
    async def goodbye_view(self, ctx: commands.Context, message_id: int):
        row = await db.get_event_message("goodbye_messages", message_id)
        if row is None:
            return await ctx.send("No goodbye message with that ID.")
        await ctx.send(f"**Channel:** <#{row['channel_id']}>\n**Message:** {row['message']}")

    @goodbye.command(name="list")
    async def goodbye_list(self, ctx: commands.Context):
        rows = await db.list_event_messages("goodbye_messages", ctx.guild.id)
        if not rows:
            return await ctx.send("No goodbye messages configured.")
        lines = [f"`#{r['id']}` → <#{r['channel_id']}>: {r['message'][:80]}" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @commands.hybrid_group(name="boost", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def boost(self, ctx: commands.Context):
        await ctx.invoke(self.boost_list)

    @boost.command(name="add")
    @app_commands.describe(channel="Channel to announce boosts in", message="Use {user}, {username}, {server}, {membercount}")
    async def boost_add(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        new_id = await db.add_event_message("boost_messages", ctx.guild.id, channel.id, message)
        await ctx.send(f"Added boost message `#{new_id}` for {channel.mention}.")

    @boost.command(name="remove")
    @app_commands.describe(message_id="ID shown in ,boost list")
    async def boost_remove(self, ctx: commands.Context, message_id: int):
        await db.remove_event_message("boost_messages", message_id)
        await ctx.send(f"Removed boost message `#{message_id}`.")

    @boost.command(name="view")
    @app_commands.describe(message_id="ID shown in ,boost list")
    async def boost_view(self, ctx: commands.Context, message_id: int):
        row = await db.get_event_message("boost_messages", message_id)
        if row is None:
            return await ctx.send("No boost message with that ID.")
        await ctx.send(f"**Channel:** <#{row['channel_id']}>\n**Message:** {row['message']}")

    @boost.command(name="list")
    async def boost_list(self, ctx: commands.Context):
        rows = await db.list_event_messages("boost_messages", ctx.guild.id)
        if not rows:
            return await ctx.send("No boost messages configured.")
        lines = [f"`#{r['id']}` → <#{r['channel_id']}>: {r['message'][:80]}" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        row = await db.get_guild_config(member.guild.id)
        if row["welcome_channel_id"] and row["welcome_message"]:
            channel = member.guild.get_channel(row["welcome_channel_id"])
            if channel:
                await channel.send(render(row["welcome_message"], member))
        for msg_row in await db.list_event_messages("welcome_messages", member.guild.id):
            channel = member.guild.get_channel(msg_row["channel_id"])
            if channel:
                await channel.send(render(msg_row["message"], member))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        row = await db.get_guild_config(member.guild.id)
        if row["goodbye_channel_id"] and row["goodbye_message"]:
            channel = member.guild.get_channel(row["goodbye_channel_id"])
            if channel:
                await channel.send(render(row["goodbye_message"], member))
        for msg_row in await db.list_event_messages("goodbye_messages", member.guild.id):
            channel = member.guild.get_channel(msg_row["channel_id"])
            if channel:
                await channel.send(render(msg_row["message"], member))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since is None and after.premium_since is not None:
            for msg_row in await db.list_event_messages("boost_messages", after.guild.id):
                channel = after.guild.get_channel(msg_row["channel_id"])
                if channel:
                    await channel.send(render(msg_row["message"], after))

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
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        rows = await db.get_autoresponders(message.guild.id)
        content = message.content.lower()
        for row in rows:
            if row["exact_match"] and content == row["trigger"]:
                return await message.channel.send(row["response"])
            elif not row["exact_match"] and row["trigger"] in content:
                return await message.channel.send(row["response"])


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
