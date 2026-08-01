import uuid

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Tickets(commands.Cog):
    """Ticket panels: members click a button to open a private support channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="ticketpanel", description="Post a panel members can click to open a ticket.")
    @commands.has_permissions(manage_channels=True)
    @app_commands.describe(
        category="Category tickets will be created under",
        support_role="Role that can see and manage tickets",
        title="Panel embed title",
        description="Panel embed description",
        channel="Channel to post the panel in (defaults to here)",
    )
    async def ticketpanel(self, ctx: commands.Context, category: discord.CategoryChannel,
                           support_role: discord.Role, title: str = "Support",
                           description: str = "Click the button below to open a ticket.",
                           channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        custom_id = f"ticketopen_{uuid.uuid4().hex[:16]}"

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Open Ticket", custom_id=custom_id, style=discord.ButtonStyle.success, emoji="🎫"))

        embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
        message = await channel.send(embed=embed, view=view)

        await db.add_ticket_panel(
            ctx.guild.id, channel.id, message.id, category.id, support_role.id,
            title, description, "Open Ticket", custom_id,
        )
        await ctx.send(f"Ticket panel posted in {channel.mention}.")

    @commands.hybrid_command(name="ticketclose", description="Close the current ticket.")
    async def ticketclose(self, ctx: commands.Context):
        ticket = await db.get_ticket(ctx.channel.id)
        if ticket is None or ticket["status"] != "open":
            return await ctx.send("This isn't an open ticket channel.")
        await ctx.send("🔒 Closing this ticket in 5 seconds...")
        await db.close_ticket(ctx.channel.id)
        import asyncio
        await asyncio.sleep(5)
        await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")

    @commands.hybrid_command(name="ticketadd", description="Add a member to the current ticket.")
    @app_commands.describe(member="Member to add to this ticket")
    async def ticketadd(self, ctx: commands.Context, member: discord.Member):
        ticket = await db.get_ticket(ctx.channel.id)
        if ticket is None:
            return await ctx.send("This isn't a ticket channel.")
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await ctx.send(f"Added {member.mention} to the ticket.")

    @commands.hybrid_command(name="ticketremove", description="Remove a member from the current ticket.")
    @app_commands.describe(member="Member to remove from this ticket")
    async def ticketremove(self, ctx: commands.Context, member: discord.Member):
        ticket = await db.get_ticket(ctx.channel.id)
        if ticket is None:
            return await ctx.send("This isn't a ticket channel.")
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(f"Removed {member.mention} from the ticket.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("ticketopen_"):
            return

        panel = await db.get_ticket_panel(custom_id)
        if panel is None:
            return await interaction.response.send_message("This panel is no longer configured.", ephemeral=True)

        guild = interaction.guild
        existing = await db.get_open_ticket_for_user(guild.id, interaction.user.id, panel["id"])
        if existing:
            existing_channel = guild.get_channel(existing["channel_id"])
            if existing_channel:
                return await interaction.response.send_message(
                    f"You already have an open ticket: {existing_channel.mention}", ephemeral=True
                )

        await interaction.response.send_message("Opening your ticket...", ephemeral=True)

        category = guild.get_channel(panel["category_id"])
        support_role = guild.get_role(panel["support_role_id"])

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel_name = f"ticket-{interaction.user.name}"[:100]
        ticket_channel = await guild.create_text_channel(
            channel_name, category=category, overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user}",
        )
        await db.create_ticket(guild.id, ticket_channel.id, interaction.user.id, panel["id"])

        embed = discord.Embed(
            title="New Ticket",
            description=f"{interaction.user.mention} welcome! {support_role.mention if support_role else ''} will be with you shortly.\n\n"
                        f"Use `/ticketclose` when this is resolved.",
            color=discord.Color.green(),
        )
        await ticket_channel.send(
            content=f"{interaction.user.mention} {support_role.mention if support_role else ''}",
            embed=embed,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
