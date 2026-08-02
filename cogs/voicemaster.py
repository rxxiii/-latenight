import discord
from discord import app_commands
from discord.ext import commands

from database import db

VM_INTERFACE_PREFIX = "vm_"


def render_channel_name(template: str, member: discord.Member) -> str:
    return (template or "{user}'s Channel").replace("{user}", member.display_name)[:100]


class VoiceMasterInterfaceView(discord.ui.View):
    """Static control panel — one message, works for whoever clicks it based
    on the voice channel they're currently sitting in."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(emoji="🔒", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}lock", row=0)
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "lock")

    @discord.ui.button(emoji="🔓", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}unlock", row=0)
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "unlock")

    @discord.ui.button(emoji="👻", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}ghost", row=0)
    async def ghost_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "ghost")

    @discord.ui.button(emoji="👁️", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}unghost", row=0)
    async def unghost_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "unghost")

    @discord.ui.button(emoji="🎙️", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}claim", row=0)
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "claim")

    @discord.ui.button(emoji="ℹ️", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}info", row=1)
    async def info_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "info")

    @discord.ui.button(emoji="➕", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}increase", row=1)
    async def increase_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "increase")

    @discord.ui.button(emoji="➖", style=discord.ButtonStyle.secondary, custom_id=f"{VM_INTERFACE_PREFIX}decrease", row=1)
    async def decrease_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await VoiceMaster.handle_interface_action(interaction, "decrease")


class VoiceMaster(commands.Cog):
    """Join a 'Join to Create' channel to get your own temporary voice
    channel, plus a full control panel and ,voicemaster command group."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(VoiceMasterInterfaceView())

    async def _get_owned_channel(self, ctx: commands.Context):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("You need to be in your temp voice channel to use this.")
            return None
        temp = await db.get_voicemaster_channel(ctx.author.voice.channel.id)
        if temp is None or temp["owner_id"] != ctx.author.id:
            await ctx.send("You don't own this voice channel.")
            return None
        return ctx.author.voice.channel

    # ---------- shared logic used by both the interface buttons and text commands ----------

    @staticmethod
    async def handle_interface_action(interaction: discord.Interaction, action: str):
        member = interaction.user
        if member.voice is None or member.voice.channel is None:
            return await interaction.response.send_message("You need to be in a voice channel to use this.", ephemeral=True)
        channel = member.voice.channel
        temp = await db.get_voicemaster_channel(channel.id)
        if temp is None:
            return await interaction.response.send_message("This isn't a VoiceMaster channel.", ephemeral=True)

        is_owner = temp["owner_id"] == member.id

        if action == "claim":
            if is_owner:
                return await interaction.response.send_message("You already own this channel.", ephemeral=True)
            owner_still_here = discord.utils.get(channel.members, id=temp["owner_id"])
            if owner_still_here:
                return await interaction.response.send_message("The current owner is still here.", ephemeral=True)
            await db.remove_voicemaster_channel(channel.id)
            await db.add_voicemaster_channel(channel.id, member.guild.id, member.id)
            await channel.set_permissions(member, manage_channels=True, move_members=True, view_channel=True, connect=True)
            return await interaction.response.send_message("You're now the owner of this channel.", ephemeral=True)

        if action == "info":
            embed = discord.Embed(title=channel.name, color=discord.Color.blurple())
            embed.add_field(name="Owner", value=f"<@{temp['owner_id']}>")
            embed.add_field(name="Members", value=str(len(channel.members)))
            embed.add_field(name="Limit", value=str(channel.user_limit or "Unlimited"))
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if not is_owner:
            return await interaction.response.send_message("Only the channel owner can do that.", ephemeral=True)

        if action == "lock":
            await channel.set_permissions(member.guild.default_role, connect=False)
            await interaction.response.send_message("🔒 Locked.", ephemeral=True)
        elif action == "unlock":
            await channel.set_permissions(member.guild.default_role, connect=True)
            await interaction.response.send_message("🔓 Unlocked.", ephemeral=True)
        elif action == "ghost":
            await channel.set_permissions(member.guild.default_role, view_channel=False)
            await interaction.response.send_message("👻 Hidden.", ephemeral=True)
        elif action == "unghost":
            await channel.set_permissions(member.guild.default_role, view_channel=True)
            await interaction.response.send_message("👁️ Visible again.", ephemeral=True)
        elif action == "increase":
            new_limit = min((channel.user_limit or 0) + 1, 99)
            await channel.edit(user_limit=new_limit)
            await interaction.response.send_message(f"Limit set to {new_limit}.", ephemeral=True)
        elif action == "decrease":
            new_limit = max((channel.user_limit or 0) - 1, 0)
            await channel.edit(user_limit=new_limit)
            await interaction.response.send_message(f"Limit set to {new_limit or 'unlimited'}.", ephemeral=True)

    # ---------- command group ----------

    @commands.hybrid_group(name="voicemaster", aliases=["vm", "vc"], invoke_without_command=True)
    async def voicemaster(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @voicemaster.command(name="setup", description="Create the join-to-create channel and post the control panel.")
    @commands.has_permissions(manage_channels=True, manage_guild=True)
    async def vm_setup(self, ctx: commands.Context):
        category = await ctx.guild.create_category("Voice Channels")
        join_channel = await ctx.guild.create_voice_channel("➕ Join to Create", category=category)
        interface_channel = await ctx.guild.create_text_channel("interface", category=category)
        await db.set_guild_config(
            ctx.guild.id,
            voicemaster_category_id=category.id,
            voicemaster_join_channel_id=join_channel.id,
        )

        embed = discord.Embed(
            title="VoiceMaster Interface",
            description="Use the buttons below to control your voice channel.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Button usage",
            value=(
                "🔒 Lock — 🔓 Unlock — 👻 Ghost — 👁️ Unghost — 🎙️ Claim\n"
                "ℹ️ Info — ➕ Increase limit — ➖ Decrease limit"
            ),
            inline=False,
        )
        await interface_channel.send(embed=embed, view=VoiceMasterInterfaceView())
        await ctx.send(f"✅ VoiceMaster set up! Join {join_channel.mention} to get your own channel. Controls are in {interface_channel.mention}.")

    @voicemaster.command(name="category", description="Set which category new voice channels are created under.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.describe(category="Category for new voice channels")
    async def vm_category(self, ctx: commands.Context, category: discord.CategoryChannel):
        await db.set_guild_config(ctx.guild.id, voicemaster_category_id=category.id)
        await ctx.send(f"New voice channels will be created under **{category.name}**.")

    @voicemaster.group(name="default", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def vm_default(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @vm_default.command(name="name")
    @app_commands.describe(template="Use {user} for the member's name, e.g. \"{user}'s Room\"")
    async def vm_default_name(self, ctx: commands.Context, *, template: str):
        await db.set_guild_config(ctx.guild.id, voicemaster_default_name=template)
        await ctx.send(f"Default channel name set to: `{template}`")

    @vm_default.command(name="bitrate")
    @app_commands.describe(kbps="Bitrate in kbps (8-96 for normal servers, higher if boosted)")
    async def vm_default_bitrate(self, ctx: commands.Context, kbps: commands.Range[int, 8, 384]):
        await db.set_guild_config(ctx.guild.id, voicemaster_default_bitrate=kbps * 1000)
        await ctx.send(f"Default bitrate set to {kbps}kbps.")

    @vm_default.command(name="region")
    @app_commands.describe(region="Voice region id, e.g. us-east, rotterdam, singapore (leave blank for automatic)")
    async def vm_default_region(self, ctx: commands.Context, region: str = None):
        await db.set_guild_config(ctx.guild.id, voicemaster_default_region=region)
        await ctx.send(f"Default region set to **{region or 'automatic'}**.")

    @voicemaster.command(name="joinrole", description="Give members a role automatically while they're in any temp voice channel.")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    @app_commands.describe(role="Role to apply (leave blank to disable)")
    async def vm_joinrole(self, ctx: commands.Context, role: discord.Role = None):
        await db.set_guild_config(ctx.guild.id, voicemaster_join_role_id=role.id if role else None)
        await ctx.send(f"Join role set to {role.mention}." if role else "Join role disabled.")

    @voicemaster.command(name="rename")
    @app_commands.describe(name="New channel name")
    async def vm_rename(self, ctx: commands.Context, *, name: str):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.edit(name=name[:100])
        await ctx.send(f"Renamed to **{name}**.")

    @voicemaster.command(name="limit")
    @app_commands.describe(limit="Max members (0-99, 0 = unlimited)")
    async def vm_limit(self, ctx: commands.Context, limit: commands.Range[int, 0, 99]):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.edit(user_limit=limit)
        await ctx.send(f"User limit set to {limit or 'unlimited'}.")

    @voicemaster.command(name="lock")
    async def vm_lock(self, ctx: commands.Context):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, connect=False)
        await ctx.send("🔒 Voice channel locked.")

    @voicemaster.command(name="unlock")
    async def vm_unlock(self, ctx: commands.Context):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, connect=True)
        await ctx.send("🔓 Voice channel unlocked.")

    @voicemaster.command(name="ghost")
    async def vm_ghost(self, ctx: commands.Context):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, view_channel=False)
        await ctx.send("👻 Voice channel hidden.")

    @voicemaster.command(name="unghost")
    async def vm_unghost(self, ctx: commands.Context):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, view_channel=True)
        await ctx.send("👁️ Voice channel visible again.")

    @voicemaster.command(name="permit")
    @app_commands.describe(member="Member to allow into your locked/hidden channel")
    async def vm_permit(self, ctx: commands.Context, member: discord.Member):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(member, view_channel=True, connect=True)
        await ctx.send(f"{member.mention} is permitted into your channel.")

    @voicemaster.command(name="role")
    @app_commands.describe(role="Restrict your channel to only members with this role")
    async def vm_role(self, ctx: commands.Context, role: discord.Role):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        await channel.set_permissions(ctx.guild.default_role, connect=False)
        await channel.set_permissions(role, connect=True)
        await ctx.send(f"Only members with {role.mention} can join your channel now.")

    @voicemaster.command(name="claim")
    async def vm_claim(self, ctx: commands.Context):
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

    @voicemaster.command(name="transfer")
    @app_commands.describe(member="Member to transfer ownership to")
    async def vm_transfer(self, ctx: commands.Context, member: discord.Member):
        channel = await self._get_owned_channel(ctx)
        if channel is None:
            return
        if member not in channel.members:
            return await ctx.send(f"{member.mention} needs to be in the channel to receive ownership.")
        await db.remove_voicemaster_channel(channel.id)
        await db.add_voicemaster_channel(channel.id, ctx.guild.id, member.id)
        await channel.set_permissions(member, manage_channels=True, move_members=True, view_channel=True, connect=True)
        await channel.set_permissions(ctx.author, overwrite=None)
        await ctx.send(f"Ownership transferred to {member.mention}.")

    # ---------- join-to-create / cleanup / join role ----------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        row = await db.get_guild_config(member.guild.id)
        join_channel_id = row["voicemaster_join_channel_id"]
        category_id = row["voicemaster_category_id"]
        join_role_id = row["voicemaster_join_role_id"]

        if join_channel_id and after.channel and after.channel.id == join_channel_id:
            category = member.guild.get_channel(category_id) if category_id else after.channel.category
            new_channel = await member.guild.create_voice_channel(
                render_channel_name(row["voicemaster_default_name"], member),
                category=category,
                bitrate=row["voicemaster_default_bitrate"] or None,
                rtc_region=row["voicemaster_default_region"],
                overwrites={
                    member: discord.PermissionOverwrite(manage_channels=True, move_members=True, view_channel=True, connect=True),
                },
            )
            await member.move_to(new_channel)
            await db.add_voicemaster_channel(new_channel.id, member.guild.id, member.id)

        if join_role_id:
            join_role = member.guild.get_role(join_role_id)
            if join_role:
                now_in_voice = after.channel is not None
                was_in_voice = before.channel is not None
                if now_in_voice and not was_in_voice:
                    try:
                        await member.add_roles(join_role, reason="VoiceMaster join role")
                    except discord.HTTPException:
                        pass
                elif was_in_voice and not now_in_voice:
                    try:
                        await member.remove_roles(join_role, reason="VoiceMaster join role")
                    except discord.HTTPException:
                        pass

        if before.channel:
            temp = await db.get_voicemaster_channel(before.channel.id)
            if temp and len(before.channel.members) == 0:
                await db.remove_voicemaster_channel(before.channel.id)
                try:
                    await before.channel.delete(reason="VoiceMaster temp channel empty")
                except discord.NotFound:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMaster(bot))
