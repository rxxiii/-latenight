import time

import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Utility(commands.Cog):
    """AFK status, avatar/banner lookup, and role management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- afk ----------

    @commands.hybrid_command(name="afk", description="Set yourself as AFK. Anyone who @mentions you will be told.")
    @app_commands.describe(reason="Optional reason")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        await db.set_afk(ctx.guild.id, ctx.author.id, reason, int(time.time()))
        await ctx.send(f"💤 {ctx.author.mention} is now AFK: {reason}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        # Coming back from AFK
        existing = await db.get_afk(message.guild.id, message.author.id)
        if existing:
            await db.remove_afk(message.guild.id, message.author.id)
            try:
                await message.channel.send(f"👋 Welcome back {message.author.mention}, I've removed your AFK.", delete_after=8)
            except discord.HTTPException:
                pass

        # Mentioning someone who's AFK
        for mentioned in message.mentions:
            if mentioned.bot:
                continue
            afk_row = await db.get_afk(message.guild.id, mentioned.id)
            if afk_row:
                try:
                    await message.channel.send(f"💤 {mentioned.display_name} is AFK: {afk_row['reason']}", delete_after=8)
                except discord.HTTPException:
                    pass

    # ---------- avatar / banner ----------

    @commands.hybrid_command(name="avatar", description="Show a member's avatar.")
    @app_commands.describe(member="Member to look up (defaults to you)")
    async def avatar(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=f"{member}'s avatar", color=discord.Color.blurple())
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="banner", description="Show a member's profile banner.")
    @app_commands.describe(member="Member to look up (defaults to you)")
    async def banner(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)  # banner isn't populated on cached Member objects
        if user.banner is None:
            return await ctx.send(f"**{member}** doesn't have a banner set.")
        embed = discord.Embed(title=f"{member}'s banner", color=discord.Color.blurple())
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)

    # ---------- role management ----------

    @commands.hybrid_group(name="role", invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(member="Member to give/remove the role from", role="Role to toggle")
    async def role(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        if role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("That role is higher than or equal to my own top role — I can't manage it.")
        if role in member.roles:
            await member.remove_roles(role, reason=f"Toggled by {ctx.author}")
            await ctx.send(f"Removed {role.mention} from {member.mention}.")
        else:
            await member.add_roles(role, reason=f"Toggled by {ctx.author}")
            await ctx.send(f"Gave {role.mention} to {member.mention}.")

    @role.command(name="create")
    @app_commands.describe(name="Name for the new role", color="Hex color, e.g. #ff0000 (optional)")
    async def role_create(self, ctx: commands.Context, name: str, color: str = None):
        role_color = discord.Color.default()
        if color:
            try:
                role_color = discord.Color(int(color.lstrip("#"), 16))
            except ValueError:
                return await ctx.send("That doesn't look like a valid hex color, e.g. `#ff0000`.")
        new_role = await ctx.guild.create_role(name=name, color=role_color, reason=f"Created by {ctx.author}")
        await ctx.send(f"Created role {new_role.mention}.")

    @role.command(name="name")
    @app_commands.describe(role="Role to rename", new_name="New name")
    async def role_name(self, ctx: commands.Context, role: discord.Role, *, new_name: str):
        if role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("That role is higher than or equal to my own top role — I can't manage it.")
        await role.edit(name=new_name, reason=f"Renamed by {ctx.author}")
        await ctx.send(f"Role renamed to **{new_name}**.")

    @role.command(name="icon")
    @app_commands.describe(role="Role to set an icon on", image_url="Direct URL to a PNG/JPG (server needs enough boosts for role icons)")
    async def role_icon(self, ctx: commands.Context, role: discord.Role, image_url: str):
        if role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("That role is higher than or equal to my own top role — I can't manage it.")
        if "ROLE_ICONS" not in ctx.guild.features:
            return await ctx.send("This server doesn't have enough boosts to unlock role icons.")
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.")
                    image_bytes = await resp.read()
            await role.edit(display_icon=image_bytes, reason=f"Icon set by {ctx.author}")
            await ctx.send(f"Icon set for {role.mention}.")
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't set that icon: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
