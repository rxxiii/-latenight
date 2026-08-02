import discord
from discord import app_commands
from discord.ext import commands

from database import db


class Boosters(commands.Cog):
    """Lets boosters create a personal color role, plus an auto-awarded
    'booster badge' role."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_own_role(self, ctx: commands.Context):
        row = await db.get_booster_role(ctx.guild.id, ctx.author.id)
        if row is None:
            await ctx.send("You don't have a booster role yet — use `,boosterrole <color> <name>` to create one.")
            return None
        role = ctx.guild.get_role(row["role_id"])
        if role is None:
            await db.remove_booster_role(ctx.guild.id, ctx.author.id)
            await ctx.send("Your booster role no longer exists — create a new one.")
            return None
        return role

    @commands.hybrid_command(name="boosterrole", description="Create or update your personal booster color role.")
    @app_commands.describe(color="Hex color, e.g. #ff69b4", name="Name for your role")
    async def boosterrole(self, ctx: commands.Context, color: str, *, name: str):
        if ctx.author.premium_since is None:
            return await ctx.send("This is only available to server boosters.")
        try:
            role_color = discord.Color(int(color.lstrip("#"), 16))
        except ValueError:
            return await ctx.send("That doesn't look like a valid hex color, e.g. `#ff69b4`.")

        row = await db.get_guild_config(ctx.guild.id)
        base_role = ctx.guild.get_role(row["booster_base_role_id"]) if row["booster_base_role_id"] else None

        existing = await db.get_booster_role(ctx.guild.id, ctx.author.id)
        if existing and ctx.guild.get_role(existing["role_id"]):
            role = ctx.guild.get_role(existing["role_id"])
            await role.edit(name=name, color=role_color)
        else:
            role = await ctx.guild.create_role(name=name, color=role_color, reason=f"Booster role for {ctx.author}")
            await ctx.author.add_roles(role)
            await db.set_booster_role(ctx.guild.id, ctx.author.id, role.id)

        if base_role:
            try:
                await role.edit(position=base_role.position)
            except discord.HTTPException:
                pass

        await ctx.send(f"Your booster role is ready: {role.mention}")

    @commands.hybrid_command(name="boosterrole-base", description="Set the anchor role new booster roles are positioned above.")
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(role="Anchor role")
    async def boosterrole_base(self, ctx: commands.Context, role: discord.Role):
        await db.set_guild_config(ctx.guild.id, booster_base_role_id=role.id)
        await ctx.send(f"New booster roles will be positioned near {role.mention}.")

    @commands.hybrid_command(name="boosterrole-rename", description="Rename your booster role.")
    @app_commands.describe(name="New name")
    async def boosterrole_rename(self, ctx: commands.Context, *, name: str):
        role = await self._get_own_role(ctx)
        if role is None:
            return
        await role.edit(name=name)
        await ctx.send(f"Renamed to **{name}**.")

    @commands.hybrid_command(name="boosterrole-icon", description="Set an icon on your booster role.")
    @app_commands.describe(image_url="Direct URL to a PNG/JPG (server needs enough boosts for role icons)")
    async def boosterrole_icon(self, ctx: commands.Context, image_url: str):
        role = await self._get_own_role(ctx)
        if role is None:
            return
        if "ROLE_ICONS" not in ctx.guild.features:
            return await ctx.send("This server doesn't have enough boosts to unlock role icons.")
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        return await ctx.send("Couldn't download that image.")
                    image_bytes = await resp.read()
            await role.edit(display_icon=image_bytes)
            await ctx.send("Icon updated.")
        except discord.HTTPException as e:
            await ctx.send(f"Couldn't set that icon: {e}")

    @commands.hybrid_command(name="boosterrole-remove", description="Delete your booster role.")
    async def boosterrole_remove(self, ctx: commands.Context):
        role = await self._get_own_role(ctx)
        if role is None:
            return
        await role.delete(reason=f"Removed by {ctx.author}")
        await db.remove_booster_role(ctx.guild.id, ctx.author.id)
        await ctx.send("Booster role removed.")

    @commands.hybrid_group(name="boosterrole-award", invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def boosterrole_award(self, ctx: commands.Context):
        await ctx.invoke(self.boosterrole_award_view)

    @boosterrole_award.command(name="set")
    @app_commands.describe(role="Role automatically given to anyone boosting the server")
    async def boosterrole_award_set(self, ctx: commands.Context, role: discord.Role):
        await db.set_guild_config(ctx.guild.id, booster_award_role_id=role.id)
        await ctx.send(f"Boosters will now automatically receive {role.mention}.")

    @boosterrole_award.command(name="view")
    async def boosterrole_award_view(self, ctx: commands.Context):
        row = await db.get_guild_config(ctx.guild.id)
        if not row["booster_award_role_id"]:
            return await ctx.send("No award role configured.")
        await ctx.send(f"Award role: <@&{row['booster_award_role_id']}>")

    @boosterrole_award.command(name="remove")
    async def boosterrole_award_remove(self, ctx: commands.Context):
        await db.set_guild_config(ctx.guild.id, booster_award_role_id=None)
        await ctx.send("Award role cleared.")

    @commands.hybrid_command(name="boosterrole-list", description="List all booster roles in this server.")
    async def boosterrole_list(self, ctx: commands.Context):
        rows = await db.list_booster_roles(ctx.guild.id)
        if not rows:
            return await ctx.send("No booster roles created yet.")
        lines = [f"<@&{r['role_id']}> — <@{r['user_id']}>" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @commands.hybrid_command(name="boosterrole-cleanup", description="Delete booster roles belonging to members who are no longer boosting.")
    @commands.has_permissions(manage_roles=True)
    async def boosterrole_cleanup(self, ctx: commands.Context):
        rows = await db.list_booster_roles(ctx.guild.id)
        removed = 0
        for row in rows:
            member = ctx.guild.get_member(row["user_id"])
            if member is None or member.premium_since is None:
                role = ctx.guild.get_role(row["role_id"])
                if role:
                    try:
                        await role.delete(reason="Booster role cleanup")
                    except discord.HTTPException:
                        pass
                await db.remove_booster_role(ctx.guild.id, row["user_id"])
                removed += 1
        await ctx.send(f"Cleaned up {removed} booster role(s).")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Auto-grant/remove the booster award role when boost status changes.
        if before.premium_since == after.premium_since:
            return
        row = await db.get_guild_config(after.guild.id)
        award_role = after.guild.get_role(row["booster_award_role_id"]) if row["booster_award_role_id"] else None
        if award_role is None:
            return
        try:
            if after.premium_since and award_role not in after.roles:
                await after.add_roles(award_role, reason="Started boosting")
            elif not after.premium_since and award_role in after.roles:
                await after.remove_roles(award_role, reason="Stopped boosting")
        except discord.HTTPException:
            pass


class Vanity(commands.Cog):
    """Grants roles to members who have the server's vanity phrase in their
    custom status. Requires the Presence Intent to be enabled for the bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_group(name="vanity", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def vanity(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @vanity.command(name="set")
    @app_commands.describe(phrase="Text members need in their custom status, e.g. .gg/yourserver")
    async def vanity_set(self, ctx: commands.Context, *, phrase: str):
        await db.set_guild_config(ctx.guild.id, vanity_phrase=phrase)
        await ctx.send(f"Vanity phrase set to: `{phrase}`\n(Requires the Presence Intent enabled in the Discord Developer Portal to detect statuses.)")

    @vanity.group(name="role", invoke_without_command=True)
    async def vanity_role(self, ctx: commands.Context):
        await ctx.invoke(self.vanity_role_list)

    @vanity_role.command(name="add")
    @app_commands.describe(role="Role to grant when the vanity phrase is detected")
    async def vanity_role_add(self, ctx: commands.Context, role: discord.Role):
        await db.vanity_role_add(ctx.guild.id, role.id)
        await ctx.send(f"{role.mention} will now be granted for vanity status.")

    @vanity_role.command(name="remove")
    @app_commands.describe(role="Role to stop granting")
    async def vanity_role_remove(self, ctx: commands.Context, role: discord.Role):
        await db.vanity_role_remove(ctx.guild.id, role.id)
        await ctx.send(f"{role.mention} removed from vanity roles.")

    @vanity_role.command(name="list")
    async def vanity_role_list(self, ctx: commands.Context):
        ids = await db.vanity_role_list(ctx.guild.id)
        if not ids:
            return await ctx.send("No vanity roles configured.")
        await ctx.send("\n".join(f"<@&{r}>" for r in ids))

    @vanity.command(name="message")
    @app_commands.describe(message="Message sent to the award channel, use {user} for the member")
    async def vanity_message(self, ctx: commands.Context, *, message: str):
        await db.set_guild_config(ctx.guild.id, vanity_message=message)
        await ctx.send("Vanity award message updated.")

    @vanity.group(name="award", invoke_without_command=True)
    async def vanity_award(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @vanity_award.command(name="channel")
    @app_commands.describe(channel="Channel to announce new vanity members in")
    async def vanity_award_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await db.set_guild_config(ctx.guild.id, vanity_award_channel_id=channel.id)
        await ctx.send(f"Vanity awards will be announced in {channel.mention}.")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        row = await db.get_guild_config(after.guild.id)
        phrase = row["vanity_phrase"]
        if not phrase:
            return
        role_ids = await db.vanity_role_list(after.guild.id)
        if not role_ids:
            return
        roles = [after.guild.get_role(r) for r in role_ids if after.guild.get_role(r)]
        if not roles:
            return

        status_text = ""
        for activity in after.activities:
            if isinstance(activity, discord.CustomActivity) and activity.name:
                status_text = activity.name.lower()
                break

        has_phrase = phrase.lower() in status_text
        already_has = await db.vanity_member_has(after.guild.id, after.id)

        if has_phrase and not already_has:
            try:
                await after.add_roles(*roles, reason="Vanity phrase detected")
            except discord.HTTPException:
                return
            await db.vanity_member_set(after.guild.id, after.id)
            if row["vanity_award_channel_id"]:
                channel = after.guild.get_channel(row["vanity_award_channel_id"])
                if channel:
                    text = (row["vanity_message"] or "{user} added our vanity to their status!").replace("{user}", after.mention)
                    await channel.send(text)
        elif not has_phrase and already_has:
            try:
                await after.remove_roles(*roles, reason="Vanity phrase removed")
            except discord.HTTPException:
                pass
            await db.vanity_member_clear(after.guild.id, after.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Boosters(bot))
    await bot.add_cog(Vanity(bot))
