import discord
from discord import app_commands
from discord.ext import commands

from database import db


class VoiceMaster(commands.Cog):
    """Join a 'Join to Create' channel to get your own temporary voice channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="voicemaster-setup", description="Set up VoiceMaster: creates the category and join-to-create channel.")
    @commands.has_permissions(manage_channels=True, manage_guild=True)
    async def voicemaster_setup(self, ctx: commands.Context):
        category = await ctx.guild.create_category("Voice Channels")
        join_channel = await ctx.guild.create_voice_channel("➕ Join to Create", category=category)
        await db.set_guild_config(
            ctx.guild.id,
            voicemaster_category_id=category.id,
            voicemaster_join_channel_id=join_channel.id,
        )
        await ctx.send(f"VoiceMaster is set up! Join {join_channel.mention} to create your own voice channel.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        row = await db.get_guild_config(member.guild.id)
        join_channel_id = row["voicemaster_join_channel_id"]
        category_id = row["voicemaster_category_id"]

        # Joined the "Join to Create" channel -> make them a new channel
        if join_channel_id and after.channel and after.channel.id == join_channel_id:
            category = member.guild.get_channel(category_id) if category_id else after.channel.category
            new_channel = await member.guild.create_voice_channel(
                f"{member.display_name}'s Channel",
                category=category,
                overwrites={
                    member: discord.PermissionOverwrite(manage_channels=True, move_members=True, view_channel=True, connect=True),
                },
            )
            await member.move_to(new_channel)
            await db.add_voicemaster_channel(new_channel.id, member.guild.id, member.id)

        # Left a temp channel -> delete it if now empty
        if before.channel:
            temp = await db.get_voicemaster_channel(before.channel.id)
            if temp and len(before.channel.members) == 0:
                await db.remove_voicemaster_channel(before.channel.id)
                try:
                    await before.channel.delete(reason="VoiceMaster temp channel empty")
                except discord.NotFound:
                    pass

    async def _get_owned_channel(self, ctx: commands.Context) -> discord.VoiceChannel | None:
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("You need to be in your temp voice channel to use this.")
            return None
        temp = await db.get_voicemaster_channel(ctx.author.voice.channel.id)
        if temp is None or temp["owner_id"] != ctx.author.id:
            await ctx.send("You don't own this voice channel.")
            return None
        return ctx.author.voice.channel

    @commands.hybrid_command(name="voice-lock", description="Lock your temp voice channel.")
    async def voice_lock(self, ctx: commands.Context):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, connect=False)
        await ctx.send("🔒 Voice channel locked.")

    @commands.hybrid_command(name="voice-unlock", description="Unlock your temp voice channel.")
    async def voice_unlock(self, ctx: commands.Context):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, connect=True)
        await ctx.send("🔓 Voice channel unlocked.")

    @commands.hybrid_command(name="voice-limit", description="Set a user limit on your temp voice channel (0 = unlimited).")
    @app_commands.describe(limit="Max members (0-99, 0 = unlimited)")
    async def voice_limit(self, ctx: commands.Context, limit: app_commands.Range[int, 0, 99]):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.edit(user_limit=limit)
        await ctx.send(f"User limit set to {limit or 'unlimited'}.")

    @commands.hybrid_command(name="voice-rename", description="Rename your temp voice channel.")
    @app_commands.describe(name="New channel name")
    async def voice_rename(self, ctx: commands.Context, *, name: str):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.edit(name=name[:100])
        await ctx.send(f"Renamed to **{name}**.")

    @commands.hybrid_command(name="voice-kick", description="Kick a member from your temp voice channel.")
    @app_commands.describe(member="Member to kick from the voice channel")
    async def voice_kick(self, ctx: commands.Context, member: discord.Member):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        if member.voice and member.voice.channel and member.voice.channel.id == channel.id:
            await member.move_to(None)
            await ctx.send(f"Kicked {member.mention} from the voice channel.")
        else:
            await ctx.send(f"{member.mention} isn't in your voice channel.")

    @commands.hybrid_command(name="voice-claim", description="Claim ownership of this temp voice channel if the owner left.")
    async def voice_claim(self, ctx: commands.Context):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            return await ctx.send("You need to be in a temp voice channel to claim it.")
        channel = ctx.author.voice.channel
        temp = await db.get_voicemaster_channel(channel.id)
        if temp is None:
            return await ctx.send("This isn't a VoiceMaster temp channel.")
        owner_still_here = discord.utils.get(channel.members, id=temp["owner_id"])
        if owner_still_here:
            return await ctx.send("The current owner is still in the channel.")
        await db.remove_voicemaster_channel(channel.id)
        await db.add_voicemaster_channel(channel.id, ctx.guild.id, ctx.author.id)
        await channel.set_permissions(ctx.author, manage_channels=True, move_members=True, view_channel=True, connect=True)
        await ctx.send(f"{ctx.author.mention} is now the owner of this channel.")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMaster(bot))
