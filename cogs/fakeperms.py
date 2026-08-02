import discord
from discord import app_commands
from discord.ext import commands

from database import db

# The set of "fake" permissions we recognize — these map onto the same
# permission checks the moderation commands already use natively.
KNOWN_PERMISSIONS = [
    "ban_members", "kick_members", "moderate_members", "manage_messages",
    "manage_roles", "manage_channels", "manage_guild", "administrator",
]


def fake_or_real_permission(permission: str):
    """A command check that passes if the invoker has the real Discord
    permission OR a role that's been granted this permission via
    ,fakepermissions add."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        if getattr(ctx.author.guild_permissions, permission, False):
            return True
        role_ids = [r.id for r in ctx.author.roles]
        return await db.has_fake_permission(ctx.guild.id, role_ids, permission)

    return commands.check(predicate)


class FakePermissions(commands.Cog):
    """Grants a role bot-command access for specific permissions without
    giving them the real Discord permission. Currently applied to:
    ban_members, kick_members, moderate_members, manage_messages
    (i.e. ban, kick, timeout, warn, purge)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_group(name="fakepermissions", aliases=["fakeperms"], invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def fakepermissions(self, ctx: commands.Context):
        await ctx.invoke(self.fakepermissions_list)

    @fakepermissions.command(name="add")
    @app_commands.describe(role="Role to grant this fake permission to", permission=f"One of: {', '.join(KNOWN_PERMISSIONS)}")
    async def fakepermissions_add(self, ctx: commands.Context, role: discord.Role, permission: str):
        permission = permission.lower()
        if permission not in KNOWN_PERMISSIONS:
            return await ctx.send(f"Permission must be one of: {', '.join(KNOWN_PERMISSIONS)}")
        await db.add_fake_permission(ctx.guild.id, role.id, permission)
        await ctx.send(f"{role.mention} now has the fake permission `{permission}`.")

    @fakepermissions.command(name="remove")
    @app_commands.describe(role="Role to remove the fake permission from", permission=f"One of: {', '.join(KNOWN_PERMISSIONS)}")
    async def fakepermissions_remove(self, ctx: commands.Context, role: discord.Role, permission: str):
        await db.remove_fake_permission(ctx.guild.id, role.id, permission.lower())
        await ctx.send(f"Removed `{permission}` from {role.mention}.")

    @fakepermissions.command(name="list")
    @app_commands.describe(role="Show only this role's fake permissions (optional)")
    async def fakepermissions_list(self, ctx: commands.Context, role: discord.Role = None):
        rows = await db.list_fake_permissions(ctx.guild.id, role.id if role else None)
        if not rows:
            return await ctx.send("No fake permissions configured.")
        lines = [f"<@&{r['role_id']}> — `{r['permission']}`" for r in rows]
        await ctx.send("\n".join(lines)[:2000])

    @fakepermissions.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def fakepermissions_reset(self, ctx: commands.Context):
        await db.reset_fake_permissions(ctx.guild.id)
        await ctx.send("All fake permissions cleared.")


async def setup(bot: commands.Bot):
    await bot.add_cog(FakePermissions(bot))
