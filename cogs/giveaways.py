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


def build_giveaway_embed(giveaway) -> discord.Embed:
    ends_at = discord.utils.utcnow().fromtimestamp(giveaway["end_time"], tz=discord.utils.utcnow().tzinfo)
    description = giveaway["description"] or ""
    embed = discord.Embed(
        title="🎉 Giveaway 🎉",
        description=(
            f"**Prize:** {giveaway['prize']}\n**Winners:** {giveaway['winner_count']}\n"
            f"**Ends:** {discord.utils.format_dt(ends_at, 'R')}\n"
            f"**Host:** <@{giveaway['host_id']}>\n"
            + (f"\n{description}" if description else "")
            + (f"\n**Required role(s):** {', '.join(f'<@&{r}>' for r in giveaway['required_roles'].split(','))}"
               if giveaway["required_roles"] else "")
            + "\n\nClick 🎉 Enter below to join!"
        ),
        color=discord.Color.fuchsia(),
    )
    if giveaway["thumbnail_url"]:
        embed.set_thumbnail(url=giveaway["thumbnail_url"])
    if giveaway["image_url"]:
        embed.set_image(url=giveaway["image_url"])
    return embed


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_message_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="🎉 Enter", style=discord.ButtonStyle.blurple,
            custom_id=f"{GIVEAWAY_BUTTON_ID}_{giveaway_message_id}",
        ))


class Giveaways(commands.Cog):
    """Button-entry giveaways: create, edit, end, reroll, cancel, list."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    @commands.hybrid_group(name="giveaway", invoke_without_command=True)
    async def giveaway(self, ctx: commands.Context):
        await ctx.invoke(self.giveaway_list)

    @giveaway.command(name="start", description="Start a giveaway.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(duration="e.g. 30m, 2h, 1d", winners="Number of winners", prize="What's being given away")
    async def giveaway_start(self, ctx: commands.Context, duration: str, winners: commands.Range[int, 1, 20], *, prize: str):
        try:
            seconds = parse_duration(duration)
        except ValueError as e:
            return await ctx.send(str(e))

        end_time = int(time.time()) + seconds
        placeholder = {
            "prize": prize, "winner_count": winners, "host_id": ctx.author.id,
            "end_time": end_time, "description": None, "thumbnail_url": None,
            "image_url": None, "required_roles": None,
        }
        embed = build_giveaway_embed(placeholder)
        message = await ctx.channel.send(embed=embed)
        view = GiveawayView(message.id)
        await message.edit(view=view)

        await db.create_giveaway(ctx.guild.id, ctx.channel.id, message.id, prize, winners, ctx.author.id, end_time)
        if ctx.interaction:
            await ctx.send("Giveaway started!", ephemeral=True)

    @giveaway.group(name="edit", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def giveaway_edit(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    async def _refresh_embed(self, message_id: int):
        giveaway = await db.get_giveaway(message_id)
        if giveaway is None or giveaway["ended"]:
            return
        guild = self.bot.get_guild(giveaway["guild_id"])
        channel = guild.get_channel(giveaway["channel_id"]) if guild else None
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=build_giveaway_embed(giveaway))
        except (discord.NotFound, discord.Forbidden):
            pass

    @giveaway_edit.command(name="host")
    @app_commands.describe(message_id="Giveaway message ID", host="New host")
    async def edit_host(self, ctx: commands.Context, message_id: str, host: discord.Member):
        await db.edit_giveaway(int(message_id), host_id=host.id)
        await self._refresh_embed(int(message_id))
        await ctx.send(f"Host updated to {host.mention}.")

    @giveaway_edit.command(name="prize")
    @app_commands.describe(message_id="Giveaway message ID", prize="New prize")
    async def edit_prize(self, ctx: commands.Context, message_id: str, *, prize: str):
        await db.edit_giveaway(int(message_id), prize=prize)
        await self._refresh_embed(int(message_id))
        await ctx.send(f"Prize updated to **{prize}**.")

    @giveaway_edit.command(name="duration")
    @app_commands.describe(message_id="Giveaway message ID", duration="New duration from now, e.g. 2h")
    async def edit_duration(self, ctx: commands.Context, message_id: str, duration: str):
        try:
            seconds = parse_duration(duration)
        except ValueError as e:
            return await ctx.send(str(e))
        new_end = int(time.time()) + seconds
        await db.edit_giveaway(int(message_id), end_time=new_end)
        await self._refresh_embed(int(message_id))
        await ctx.send(f"New end time set: in {duration}.")

    @giveaway_edit.command(name="winners")
    @app_commands.describe(message_id="Giveaway message ID", count="New winner count")
    async def edit_winners(self, ctx: commands.Context, message_id: str, count: commands.Range[int, 1, 20]):
        await db.edit_giveaway(int(message_id), winner_count=count)
        await self._refresh_embed(int(message_id))
        await ctx.send(f"Winner count updated to **{count}**.")

    @giveaway_edit.command(name="description")
    @app_commands.describe(message_id="Giveaway message ID", description="New description")
    async def edit_description(self, ctx: commands.Context, message_id: str, *, description: str):
        await db.edit_giveaway(int(message_id), description=description)
        await self._refresh_embed(int(message_id))
        await ctx.send("Description updated.")

    @giveaway_edit.command(name="thumbnail")
    @app_commands.describe(message_id="Giveaway message ID", url="Image URL")
    async def edit_thumbnail(self, ctx: commands.Context, message_id: str, url: str):
        await db.edit_giveaway(int(message_id), thumbnail_url=url)
        await self._refresh_embed(int(message_id))
        await ctx.send("Thumbnail updated.")

    @giveaway_edit.command(name="image")
    @app_commands.describe(message_id="Giveaway message ID", url="Image URL")
    async def edit_image(self, ctx: commands.Context, message_id: str, url: str):
        await db.edit_giveaway(int(message_id), image_url=url)
        await self._refresh_embed(int(message_id))
        await ctx.send("Image updated.")

    @giveaway_edit.command(name="requiredroles")
    @app_commands.describe(message_id="Giveaway message ID", roles="Mention the required roles, space separated")
    async def edit_required_roles(self, ctx: commands.Context, message_id: str, roles: commands.Greedy[discord.Role]):
        role_csv = ",".join(str(r.id) for r in roles) if roles else None
        await db.edit_giveaway(int(message_id), required_roles=role_csv)
        await self._refresh_embed(int(message_id))
        if roles:
            await ctx.send(f"Required roles set: {', '.join(r.mention for r in roles)}")
        else:
            await ctx.send("Required roles cleared — anyone can enter now.")

    @giveaway.command(name="end", description="End a giveaway early.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID of the giveaway message")
    async def giveaway_end(self, ctx: commands.Context, message_id: str):
        giveaway = await db.get_giveaway(int(message_id))
        if giveaway is None or giveaway["ended"]:
            return await ctx.send("That giveaway isn't active.")
        await self._finish_giveaway(giveaway)
        await ctx.send("Giveaway ended.")

    @giveaway.command(name="reroll", description="Reroll the winner(s) of an ended giveaway.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID of the giveaway message")
    async def giveaway_reroll(self, ctx: commands.Context, message_id: str):
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

    @giveaway.command(name="cancel", description="Cancel a giveaway with no winner picked.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID of the giveaway message")
    async def giveaway_cancel(self, ctx: commands.Context, message_id: str):
        giveaway = await db.get_giveaway(int(message_id))
        if giveaway is None:
            return await ctx.send("Giveaway not found.")
        guild = ctx.guild
        channel = guild.get_channel(giveaway["channel_id"])
        if channel:
            try:
                message = await channel.fetch_message(int(message_id))
                embed = discord.Embed(
                    title="🚫 Giveaway Cancelled",
                    description=f"**Prize:** {giveaway['prize']}",
                    color=discord.Color.dark_grey(),
                )
                await message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden):
                pass
        await db.delete_giveaway(int(message_id))
        await ctx.send("Giveaway cancelled — no winner picked.")

    @giveaway.command(name="list", description="List active giveaways in this server.")
    async def giveaway_list(self, ctx: commands.Context):
        active = await db.get_active_giveaways_for_guild(ctx.guild.id)
        if not active:
            return await ctx.send("No active giveaways.")
        lines = []
        for g in active:
            lines.append(f"**{g['prize']}** — ends {discord.utils.format_dt(discord.utils.utcnow().fromtimestamp(g['end_time']), 'R')} — `{g['message_id']}`")
        await ctx.send("\n".join(lines)[:2000])

    # ---------- entry handling ----------

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

        if giveaway["required_roles"]:
            required_ids = {int(r) for r in giveaway["required_roles"].split(",") if r}
            member_role_ids = {r.id for r in interaction.user.roles}
            if not required_ids & member_role_ids:
                role_mentions = ", ".join(f"<@&{r}>" for r in required_ids)
                return await interaction.response.send_message(
                    f"You need one of these roles to enter: {role_mentions}", ephemeral=True
                )

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
