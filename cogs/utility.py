import time

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from cogs.moderation import hierarchy_ok



def _no_role_pings():
    return discord.AllowedMentions.none()


async def _bleed_role_embed(ctx: commands.Context, text: str):
    embed = discord.Embed(description=text, color=discord.Color.blurple())
    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def find_role(guild: discord.Guild, query: str) -> discord.Role | None:
    """Resolve a role from a mention/ID/exact name first, then fall back to
    startswith/contains matching so ',role @user pic' can still find a role
    named 'pics'."""
    query = query.strip()
    if query.startswith("<@&") and query.endswith(">"):
        try:
            return guild.get_role(int(query[3:-1]))
        except ValueError:
            pass
    if query.isdigit():
        role = guild.get_role(int(query))
        if role:
            return role

    query_lower = query.lower()
    for role in guild.roles:
        if role.name.lower() == query_lower:
            return role
    for role in guild.roles:
        if role.name.lower().startswith(query_lower):
            return role
    for role in guild.roles:
        if query_lower in role.name.lower():
            return role
    return None


class Utility(commands.Cog):
    """AFK status, avatar/banner lookup, and role management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- afk ----------

    @commands.hybrid_command(name="afk", description="Set yourself as AFK. Anyone who @mentions you will be told.")
    @app_commands.describe(reason="Optional reason")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        await db.set_afk(ctx.guild.id, ctx.author.id, reason, int(time.time()))
        embed = discord.Embed(
            description=f"✅ {ctx.author.mention}: You're now AFK with the status: **{reason}**",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, allowed_mentions=_no_role_pings())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        # Coming back from AFK
        existing = await db.get_afk(message.guild.id, message.author.id)
        if existing:
            await db.remove_afk(message.guild.id, message.author.id)
            try:
                embed = discord.Embed(
                    description=f"✅ Welcome back {message.author.mention}, I've removed your AFK.",
                    color=discord.Color.green(),
                )
                await message.channel.send(embed=embed, delete_after=8)
            except discord.HTTPException:
                pass

        # Mentioning someone who's AFK
        for mentioned in message.mentions:
            if mentioned.bot:
                continue
            afk_row = await db.get_afk(message.guild.id, mentioned.id)
            if afk_row:
                try:
                    embed = discord.Embed(
                        description=f"💤 {mentioned.mention} is AFK: **{afk_row['reason']}**",
                        color=discord.Color.greyple(),
                    )
                    await message.channel.send(embed=embed, delete_after=8)
                except discord.HTTPException:
                    pass

    # ---------- avatar / banner ----------

    @commands.hybrid_command(name="avatar", aliases=["av"], description="Show a member's global (account) avatar.")
    @app_commands.describe(member="Member to look up (defaults to you)")
    async def avatar(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)  # a clean User object always has the global avatar, never a per-server override
        embed = discord.Embed(title=f"{member}'s avatar", color=discord.Color.blurple())
        embed.set_image(url=user.display_avatar.url)
        await ctx.send(embed=embed, allowed_mentions=_no_role_pings())

    @commands.hybrid_command(name="sav", description="Show a member's server-specific avatar, if they have one.")
    @app_commands.describe(member="Member to look up (defaults to you)")
    async def server_avatar(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        if member.guild_avatar is None:
            return await ctx.send(f"**{member}** doesn't have a server-specific avatar set here.", allowed_mentions=_no_role_pings())
        embed = discord.Embed(title=f"{member}'s server avatar", color=discord.Color.blurple())
        embed.set_image(url=member.guild_avatar.url)
        await ctx.send(embed=embed, allowed_mentions=_no_role_pings())

    @commands.hybrid_command(name="banner", description="Show a member's profile banner.")
    @app_commands.describe(member="Member to look up (defaults to you)")
    async def banner(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)  # banner isn't populated on cached Member objects
        if user.banner is None:
            return await ctx.send(f"**{member}** doesn't have a banner set.", allowed_mentions=_no_role_pings())
        embed = discord.Embed(title=f"{member}'s banner", color=discord.Color.blurple())
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed, allowed_mentions=_no_role_pings())

    @commands.hybrid_command(name="sbanner", description="Show a member's server-specific banner, if they have one.")
    @app_commands.describe(member="Member to look up (defaults to you)")
    async def server_banner(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        guild_banner = getattr(member, "guild_banner", None)
        if guild_banner is None:
            return await ctx.send(f"**{member}** doesn't have a server-specific banner set here.", allowed_mentions=_no_role_pings())
        embed = discord.Embed(title=f"{member}'s server banner", color=discord.Color.blurple())
        embed.set_image(url=guild_banner.url)
        await ctx.send(embed=embed, allowed_mentions=_no_role_pings())

    # ---------- role management ----------

    @commands.hybrid_group(name="role", aliases=["r"], invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(member="Member to give/remove the role from", role="Role to toggle (name, partial name, mention, or ID)")
    async def role(self, ctx: commands.Context, member: discord.Member, *, role: str):
        found_role = await find_role(ctx.guild, role)
        if found_role is None:
            return await ctx.send(f"Couldn't find a role matching `{role}`.", allowed_mentions=_no_role_pings())

        ok, error = hierarchy_ok(ctx, member)
        if not ok:
            return await ctx.send(error, allowed_mentions=_no_role_pings())
        if found_role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("That role is higher than or equal to my own top role — I can't manage it.", allowed_mentions=_no_role_pings())
        if found_role in member.roles:
            await member.remove_roles(found_role, reason=f"Toggled by {ctx.author}")
            await _bleed_role_embed(
                ctx,
                f"➖ {ctx.author.mention}: Removed {found_role.mention} from {member.mention}",
            )
        else:
            await member.add_roles(found_role, reason=f"Toggled by {ctx.author}")
            await _bleed_role_embed(
                ctx,
                f"➕ {ctx.author.mention}: Added {found_role.mention} to {member.mention}",
            )

    @role.command(name="create")
    @app_commands.describe(name="Name for the new role", color="Hex color, e.g. #ff0000 (optional)")
    async def role_create(self, ctx: commands.Context, name: str, color: str = None):
        role_color = discord.Color.default()
        if color:
            try:
                role_color = discord.Color(int(color.lstrip("#"), 16))
            except ValueError:
                return await ctx.send("That doesn't look like a valid hex color, e.g. `#ff0000`.", allowed_mentions=_no_role_pings())
        new_role = await ctx.guild.create_role(name=name, color=role_color, reason=f"Created by {ctx.author}")
        await ctx.send(f"Created role {new_role.mention}.", allowed_mentions=_no_role_pings())

    @role.command(name="name")
    @app_commands.describe(role="Role to rename", new_name="New name")
    async def role_name(self, ctx: commands.Context, role: discord.Role, *, new_name: str):
        if role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("That role is higher than or equal to my own top role — I can't manage it.", allowed_mentions=_no_role_pings())
        await role.edit(name=new_name, reason=f"Renamed by {ctx.author}")
        await ctx.send(f"Role renamed to **{new_name}**.", allowed_mentions=_no_role_pings())

    @role.command(name="icon")
    @app_commands.describe(role="Role to set an icon on", image_url="Direct URL to a PNG/JPG (server needs enough boosts for role icons)")
    async def role_icon(self, ctx: commands.Context, role: discord.Role, image_url: str):
        if role.position >= ctx.guild.me.top_role.position:
            return await ctx.send("That role is higher than or equal to my own top role — I can't manage it.", allowed_mentions=_no_role_pings())
        if "ROLE_ICONS" not in ctx.guild.features:
            return await ctx.send("This server doesn't have enough boosts to unlock role icons.", allowed_mentions=_no_role_pings())
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.", allowed_mentions=_no_role_pings())
                    image_bytes = await resp.read()
            await role.edit(display_icon=image_bytes, reason=f"Icon set by {ctx.author}")
            await ctx.send(f"Icon set for {role.mention}.", allowed_mentions=_no_role_pings())
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't set that icon: {e}", allowed_mentions=_no_role_pings())


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
