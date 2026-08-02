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

    @reactionrole.command(name="list")
    async def reactionrole_list(self, ctx: commands.Context):
        rows = await db.get_reaction_roles_for_guild(ctx.guild.id)
        if not rows:
            return await ctx.send("No reaction roles configured.")
        lines = [f"{r['emoji']} → <@&{r['role_id']}> (message `{r['message_id']}`)" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @reactionrole.command(name="removeall")
    @app_commands.describe(message_id="ID of the message to clear reaction roles from")
    async def reactionrole_removeall(self, ctx: commands.Context, message_id: str):
        await db.remove_reaction_roles_for_message(int(message_id))
        await ctx.send("Removed all reaction roles from that message.")

    @reactionrole.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def reactionrole_reset(self, ctx: commands.Context):
        await db.reset_reaction_roles(ctx.guild.id)
        await ctx.send("All reaction roles for this server have been reset.")

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

    @commands.hybrid_group(name="buttonrole-manage", invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def buttonrole_manage(self, ctx: commands.Context):
        await ctx.invoke(self.buttonrole_list)

    @commands.hybrid_command(name="buttonrole-list", description="List all button role bindings in this server.")
    @commands.has_permissions(manage_roles=True)
    async def buttonrole_list(self, ctx: commands.Context):
        rows = await db.get_button_roles_for_guild(ctx.guild.id)
        if not rows:
            return await ctx.send("No button roles configured.")
        lines = [f"**{r['label']}** → <@&{r['role_id']}> (message `{r['message_id']}`)" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @commands.hybrid_command(name="buttonrole-removeall", description="Remove all button roles tied to a specific message.")
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(message_id="ID of the message to clear button roles from")
    async def buttonrole_removeall(self, ctx: commands.Context, message_id: str):
        await db.remove_button_roles_for_message(int(message_id))
        await ctx.send("Removed all button roles from that message.")

    @commands.hybrid_command(name="buttonrole-remove", description="Remove a single button role from a message.")
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(message_id="ID of the message the button is on", role="Role tied to the button you want to remove")
    async def buttonrole_remove(self, ctx: commands.Context, message_id: str, role: discord.Role):
        rows = await db.get_button_roles_for_guild(ctx.guild.id)
        target = next((r for r in rows if str(r["message_id"]) == message_id and r["role_id"] == role.id), None)
        if target is None:
            return await ctx.send("Couldn't find that button role.")

        await db.remove_button_role(target["custom_id"])

        remaining = [r for r in await db.get_button_roles_for_guild(ctx.guild.id) if str(r["message_id"]) == message_id]
        channel = ctx.guild.get_channel(target["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(int(message_id))
                if remaining:
                    new_view = RoleButtonView([(r["label"], r["custom_id"]) for r in remaining])
                    await message.edit(view=new_view)
                else:
                    await message.edit(view=None)
            except (discord.NotFound, discord.Forbidden):
                pass
        await ctx.send(f"Removed the {role.mention} button.")

    @commands.hybrid_command(name="buttonrole-reset", description="Wipe every button role configured in this server.")
    @commands.has_permissions(administrator=True)
    async def buttonrole_reset(self, ctx: commands.Context):
        await db.reset_button_roles(ctx.guild.id)
        await ctx.send("All button roles for this server have been reset.")

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
    async def starboard_threshold(self, ctx: commands.Context, count: commands.Range[int, 1, 100]):
        await db.set_guild_config(ctx.guild.id, starboard_threshold=count)
        await ctx.send(f"Starboard threshold set to {count} ⭐.")

    @starboard.command(name="set")
    @app_commands.describe(channel="Channel where starred messages get reposted")
    async def starboard_set(self, ctx: commands.Context, channel: discord.TextChannel):
        await ctx.invoke(self.starboard_channel, channel=channel)

    @starboard.command(name="lock")
    async def starboard_lock(self, ctx: commands.Context):
        await db.set_guild_config(ctx.guild.id, starboard_locked=1)
        await ctx.send("🔒 Starboard locked — no new messages will be posted (existing ones still update).")

    @starboard.command(name="unlock")
    async def starboard_unlock(self, ctx: commands.Context):
        await db.set_guild_config(ctx.guild.id, starboard_locked=0)
        await ctx.send("🔓 Starboard unlocked.")

    @starboard.command(name="emoji")
    @app_commands.describe(emoji="Emoji to use instead of ⭐")
    async def starboard_emoji(self, ctx: commands.Context, emoji: str):
        await db.set_guild_config(ctx.guild.id, starboard_emoji=emoji)
        await ctx.send(f"Starboard emoji set to {emoji}")

    @starboard.command(name="selfstar")
    @app_commands.describe(state="on or off")
    async def starboard_selfstar(self, ctx: commands.Context, state: str):
        await db.set_guild_config(ctx.guild.id, starboard_selfstar=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Self-starring: **{state}**")

    @starboard.command(name="color")
    @app_commands.describe(hex_color="Hex color, e.g. #ff0000")
    async def starboard_color(self, ctx: commands.Context, hex_color: str):
        try:
            int(hex_color.lstrip("#"), 16)
        except ValueError:
            return await ctx.send("That doesn't look like a valid hex color, e.g. `#ff0000`.")
        await db.set_guild_config(ctx.guild.id, starboard_color=hex_color)
        await ctx.send(f"Starboard embed color set to `{hex_color}`.")

    @starboard.command(name="timestamp")
    @app_commands.describe(state="on or off")
    async def starboard_timestamp(self, ctx: commands.Context, state: str):
        await db.set_guild_config(ctx.guild.id, starboard_timestamp=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Timestamp display: **{state}**")

    @starboard.command(name="jumpurl")
    @app_commands.describe(state="on or off")
    async def starboard_jumpurl(self, ctx: commands.Context, state: str):
        await db.set_guild_config(ctx.guild.id, starboard_jumpurl=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Jump-to-message link: **{state}**")

    @starboard.command(name="attachments")
    @app_commands.describe(state="on or off")
    async def starboard_attachments(self, ctx: commands.Context, state: str):
        await db.set_guild_config(ctx.guild.id, starboard_attachments=1 if state.lower() in ("on", "enable", "true") else 0)
        await ctx.send(f"Attachment display: **{state}**")

    @starboard.group(name="ignore", invoke_without_command=True)
    async def starboard_ignore(self, ctx: commands.Context):
        await ctx.invoke(self.starboard_ignore_list)

    @starboard_ignore.command(name="add")
    @app_commands.describe(channel="Channel to exclude from the starboard")
    async def starboard_ignore_add(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.starboard_ignore_add(ctx.guild.id, channel.id)
        await ctx.send(f"{channel.mention} is now ignored by the starboard.")

    @starboard_ignore.command(name="remove")
    @app_commands.describe(channel="Channel to stop ignoring")
    async def starboard_ignore_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.starboard_ignore_remove(ctx.guild.id, channel.id)
        await ctx.send(f"{channel.mention} is no longer ignored.")

    @starboard_ignore.command(name="list")
    async def starboard_ignore_list(self, ctx: commands.Context):
        ids = await db.starboard_ignore_list(ctx.guild.id)
        if not ids:
            return await ctx.send("No channels are ignored.")
        await ctx.send("\n".join(f"<#{i}>" for i in ids))

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add_star(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        row = await db.get_guild_config(payload.guild_id)
        if not row["starboard_channel_id"] or row["starboard_locked"]:
            return
        if str(payload.emoji) != (row["starboard_emoji"] or "⭐"):
            return
        if await db.is_starboard_ignored(payload.guild_id, payload.channel_id):
            return

        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        star_emoji = row["starboard_emoji"] or "⭐"
        star_reaction = discord.utils.get(message.reactions, emoji=star_emoji)
        count = star_reaction.count if star_reaction else 0

        if not row["starboard_selfstar"]:
            # Recompute the count without the author's own reaction, since
            # discord.py's Reaction.count includes everyone regardless.
            if star_reaction:
                users = [u async for u in star_reaction.users()]
                if message.author in users:
                    count -= 1

        if count < row["starboard_threshold"]:
            return

        starboard_channel = guild.get_channel(row["starboard_channel_id"])
        if starboard_channel is None:
            return

        existing = await db.get_starboard_post(message.id)
        embed_color = discord.Color.gold()
        if row["starboard_color"]:
            try:
                embed_color = discord.Color(int(row["starboard_color"].lstrip("#"), 16))
            except ValueError:
                pass

        embed = discord.Embed(
            description=message.content,
            color=embed_color,
            timestamp=message.created_at if row["starboard_timestamp"] else None,
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        if row["starboard_jumpurl"]:
            embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})")
        if row["starboard_attachments"] and message.attachments:
            embed.set_image(url=message.attachments[0].url)

        content = f"{star_emoji} **{count}** | {channel.mention}"
        if existing:
            try:
                starboard_message = await starboard_channel.fetch_message(existing["starboard_message_id"])
                await starboard_message.edit(content=content, embed=embed)
            except (discord.NotFound, discord.Forbidden):
                pass
            await db.upsert_starboard_post(guild.id, message.id, existing["starboard_message_id"], count)
        else:
            starboard_message = await starboard_channel.send(content=content, embed=embed)
            await db.upsert_starboard_post(guild.id, message.id, starboard_message.id, count)


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
