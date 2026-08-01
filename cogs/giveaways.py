import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import db

GIVEAWAY_BUTTON_ID = "giveaway_enter"


def parse_duration(duration: str) -> int:
    """Parse '10m', '2h', '1d' into seconds."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    unit = duration[-1].lower()
    if unit not in units:
        raise ValueError("Duration must end in s/m/h/d/w, e.g. 30m, 2h, 1d")
    return int(float(duration[:-1]) * units[unit])


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_message_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="🎉 Enter", style=discord.ButtonStyle.blurple,
            custom_id=f"{GIVEAWAY_BUTTON_ID}_{giveaway_message_id}",
        ))


class Giveaways(commands.Cog):
    """Button-entry giveaways with automatic ending."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    @commands.hybrid_command(name="gstart", description="Start a giveaway.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(duration="e.g. 30m, 2h, 1d", winners="Number of winners", prize="What's being given away")
    async def gstart(self, ctx: commands.Context, duration: str, winners: app_commands.Range[int, 1, 20], *, prize: str):
        try:
            seconds = parse_duration(duration)
        except ValueError as e:
            return await ctx.send(str(e))

        end_time = int(time.time()) + seconds
        embed = discord.Embed(
            title="🎉 Giveaway 🎉",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\n"
                        f"**Ends:** {discord.utils.format_dt(discord.utils.utcnow().fromtimestamp(end_time), 'R')}\n\n"
                        f"Click 🎉 Enter below to join!",
            color=discord.Color.fuchsia(),
        )
        embed.set_footer(text=f"Hosted by {ctx.author}")

        message = await ctx.channel.send(embed=embed)
        view = GiveawayView(message.id)
        await message.edit(view=view)

        await db.create_giveaway(ctx.guild.id, ctx.channel.id, message.id, prize, winners, ctx.author.id, end_time)
        if ctx.interaction:
            await ctx.send("Giveaway started!", ephemeral=True)

    @commands.hybrid_command(name="gend", description="End a giveaway early.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID of the giveaway message")
    async def gend(self, ctx: commands.Context, message_id: str):
        giveaway = await db.get_giveaway(int(message_id))
        if giveaway is None or giveaway["ended"]:
            return await ctx.send("That giveaway isn't active.")
        await self._finish_giveaway(giveaway)
        await ctx.send("Giveaway ended.")

    @commands.hybrid_command(name="greroll", description="Reroll the winner(s) of an ended giveaway.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID of the giveaway message")
    async def greroll(self, ctx: commands.Context, message_id: str):
        giveaway = await db.get_giveaway(int(message_id))
        if giveaway is None:
            return await ctx.send("Giveaway not found.")
        entrants = await db.get_giveaway_entrants(giveaway["id"])
        if not entrants:
            return await ctx.send("No entrants to reroll from.")
        winners = random.sample(entrants, min(giveaway["winner_count"], len(entrants)))
        mentions = ", ".join(f"<@{w}>" for w in winners)
        channel = ctx.guild.get_channel(giveaway["channel_id"])
        await channel.send(f"🎉 New winner(s) for **{giveaway['prize']}**: {mentions}")
        await ctx.send("Rerolled.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith(f"{GIVEAWAY_BUTTON_ID}_"):
            return
        message_id = int(custom_id.split("_")[-1])
        giveaway = await db.get_giveaway(message_id)
        if giveaway is None or giveaway["ended"]:
            return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)

        already_entered = await db.has_entered_giveaway(giveaway["id"], interaction.user.id)
        if already_entered:
            await db.remove_giveaway_entry(giveaway["id"], interaction.user.id)
            await interaction.response.send_message("Entry removed — you're out of the giveaway.", ephemeral=True)
        else:
            await db.add_giveaway_entry(giveaway["id"], interaction.user.id)
            await interaction.response.send_message("You're entered! Good luck 🍀", ephemeral=True)

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        active = await db.get_active_giveaways()
        now = int(time.time())
        for giveaway in active:
            if giveaway["end_time"] <= now:
                await self._finish_giveaway(giveaway)

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()

    async def _finish_giveaway(self, giveaway):
        await db.end_giveaway(giveaway["message_id"])
        guild = self.bot.get_guild(giveaway["guild_id"])
        if guild is None:
            return
        channel = guild.get_channel(giveaway["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(giveaway["message_id"])
        except (discord.NotFound, discord.Forbidden):
            message = None

        entrants = await db.get_giveaway_entrants(giveaway["id"])
        if not entrants:
            result_text = "No valid entries — no winner could be chosen."
            winners = []
        else:
            winners = random.sample(entrants, min(giveaway["winner_count"], len(entrants)))
            result_text = ", ".join(f"<@{w}>" for w in winners)

        embed = discord.Embed(
            title="🎉 Giveaway Ended 🎉",
            description=f"**Prize:** {giveaway['prize']}\n**Winner(s):** {result_text}",
            color=discord.Color.dark_grey(),
        )
        if message:
            await message.edit(embed=embed, view=None)
        if winners:
            await channel.send(f"Congratulations {result_text}! You won **{giveaway['prize']}**!")
        else:
            await channel.send(f"No one entered the giveaway for **{giveaway['prize']}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))

