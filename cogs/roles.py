import uuid

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class RoleButtonView(discord.ui.View):
    """A generic view used only when *creating* a button-role message.

    Actual clicks (including after a bot restart) are handled by the raw
    on_interaction listener below, which looks the custom_id up in the
    database — so this view doesn't need to be re-registered as persistent.
    """

    def __init__(self, buttons: list[tuple[str, str]]):
        super().__init__(timeout=None)
        for label, custom_id in buttons:
            self.add_item(discord.ui.Button(label=label, custom_id=custom_id, style=discord.ButtonStyle.primary))


class Roles(commands.Cog):
    """Reaction roles, button roles, and starboard."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- reaction roles ----------

    @commands.hybrid_group(name="reactionrole", aliases=["rr"], invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def reactionrole(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @reactionrole.command(name="add")
    @app_commands.describe(message_id="ID of the message to react to", emoji="Emoji to react with", role="Role to grant")
    async def reactionrole_add(self, ctx: commands.Context, message_id: str, emoji: str, role: discord.Role):
        message = None
        for channel in ctx.guild.text_channels:
            try:
                message = await channel.fetch_message(int(message_id))
                break
            except (discord.NotFound, discord.Forbidden):
                continue
        if message is None:
            return await ctx.send("Couldn't find that message in this server.")
        await message.add_reaction(emoji)
        await db.add_reaction_role(ctx.guild.id, message.id, message.channel.id, emoji, role.id)
        await ctx.send(f"Reacting {emoji} on that message now grants {role.mention}.")

    @reactionrole.command(name="remove")
    @app_commands.describe(message_id="ID of the message", emoji="Emoji tied to the role")
    async def reactionrole_remove(self, ctx: commands.Context, message_id: str, emoji: str):
        await db.remove_reaction_role(int(message_id), emoji)
        await ctx.send("Reaction role removed.")

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add_role(self, payload: discord.RawReactionActionEvent):
        if payload.member is None or payload.member.bot:
            return
        row = await db.get_reaction_role(payload.message_id, str(payload.emoji))
        if row is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = guild.get_role(row["role_id"])
        if role:
            try:
                await payload.member.add_roles(role, reason="Reaction role")
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        row = await db.get_reaction_role(payload.message_id, str(payload.emoji))
        if row is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        role = guild.get_role(row["role_id"])
        if member and role:
            try:
                await member.remove_roles(role, reason="Reaction role removed")
            except discord.Forbidden:
                pass

    # ---------- button roles ----------

    @commands.hybrid_command(name="buttonrole", description="Post a message with a button that grants a role when clicked.")
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(role="Role to grant", label="Button label", channel="Channel to post in (defaults to here)")
    async def buttonrole(self, ctx: commands.Context, role: discord.Role, label: str = None,
                          channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        label = label or role.name
        custom_id = f"rolebtn_{uuid.uuid4().hex[:16]}"

        view = RoleButtonView([(label, custom_id)])
        embed = discord.Embed(
            title="Get a role",
            description=f"Click the button below to receive the **{role.name}** role.",
            color=discord.Color.blurple(),
        )
        message = await channel.send(embed=embed, view=view)
        await db.add_button_role(ctx.guild.id, message.id, channel.id, label, role.id, custom_id)
        await ctx.send(f"Button role posted in {channel.mention}.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("rolebtn_"):
            return
        row = await db.get_button_role(custom_id)
        if row is None:
            return await interaction.response.send_message("This button is no longer configured.", ephemeral=True)
        guild = interaction.guild
        role = guild.get_role(row["role_id"])
        if role is None:
            return await interaction.response.send_message("That role no longer exists.", ephemeral=True)
        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role, reason="Button role toggle")
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Button role toggle")
            await interaction.response.send_message(f"Gave you **{role.name}**.", ephemeral=True)

    # ---------- starboard ----------

    @commands.hybrid_group(name="starboard", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def starboard(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @starboard.command(name="channel")
    @app_commands.describe(channel="Channel where starred messages get reposted")
    async def starboard_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.set_guild_config(ctx.guild.id, starboard_channel_id=channel.id)
        await ctx.send(f"Starboard channel set to {channel.mention}.")

    @starboard.command(name="threshold")
    @app_commands.describe(count="Number of star reactions needed to post to the starboard")
    async def starboard_threshold(self, ctx: commands.Context, count: app_commands.Range[int, 1, 100]):
        await db.set_guild_config(ctx.guild.id, starboard_threshold=count)
        await ctx.send(f"Starboard threshold set to {count} ⭐.")

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add_star(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != "⭐" or payload.guild_id is None:
            return
        row = await db.get_guild_config(payload.guild_id)
        if not row["starboard_channel_id"]:
            return
        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        star_reaction = discord.utils.get(message.reactions, emoji="⭐")
        count = star_reaction.count if star_reaction else 0
        if count < row["starboard_threshold"]:
            return

        starboard_channel = guild.get_channel(row["starboard_channel_id"])
        if starboard_channel is None:
            return

        existing = await db.get_starboard_post(message.id)
        embed = discord.Embed(description=message.content, color=discord.Color.gold(), timestamp=message.created_at)
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})")
        if message.attachments:
            embed.set_image(url=message.attachments[0
