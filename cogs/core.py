import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Core(commands.Cog):
    """Prefix management and general utility commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_group(name="prefix", invoke_without_command=True)
    @commands.guild_only()
    async def prefix(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)
        await ctx.send(f"Current prefix: `{row['prefix']}`")

    @prefix.command(name="set")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(new_prefix="The new prefix for this server")
    async def prefix_set(self, ctx: commands.Context, new_prefix: str):
        if len(new_prefix) > 5:
            return await ctx.send("Prefix must be 5 characters or fewer.")
        await db.set_guild_config(ctx.guild.id, prefix=new_prefix)
        await ctx.send(f"Prefix updated to `{new_prefix}`.")

    @commands.hybrid_command(name="say", description="Make the bot say something.")
    @commands.has_permissions(manage_messages=True)
    @app_commands.describe(message="What the bot should say", channel="Channel to send it in (defaults to here)")
    async def say(self, ctx: commands.Context, message: str, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        if ctx.interaction:
            await channel.send(message)
            await ctx.send("Sent.", ephemeral=True)
        else:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            await channel.send(message)

    @commands.hybrid_command(name="ping", description="Check the bot's latency.")
    async def ping(self, ctx: commands.Context):
        await ctx.send(f"Pong! `{round(self.bot.latency * 1000)}ms`")

    @commands.hybrid_command(name="userinfo", description="Show information about a member.")
    @app_commands.describe(member="The member to look up (defaults to you)")
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=str(member), color=discord.Color.blurple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(
            name="Joined server",
            value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Account created",
            value=discord.utils.format_dt(member.created_at, "R"),
            inline=True,
        )
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        embed.add_field(name=f"Roles [{len(roles)}]", value=" ".join(roles) or "None", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Show information about this server.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=str(guild.owner), inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"), inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
