"""
Public-data lookup commands: Roblox username info, DNS resolution, and
public IP geolocation. Everything here queries public, non-sensitive data
(Roblox's own public API, standard DNS, and public IP geolocation) — no
breach data, credentials, or private personal information.

Commands:
    ,osint username <username>
    ,osint domain <domain>
    ,osint ip <ip>
    ,roblox <discord_id>   (placeholder — see note below)

The ,roblox command reads from an in-memory link table that nothing in
this file populates. It's a placeholder for a future verification flow
where a user proves they own a Roblox account (e.g. via a bio/status code
challenge) before being linked — until that's built, it will always report
"no account linked."
"""

import ipaddress
import re
import socket
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


class OSINT(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.roblox_links: dict[int, dict] = {}  # {discord_id: roblox_user_dict}
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def _json(self, url, *, method="GET", payload=None):
        if method == "POST":
            async with self.session.post(url, json=payload) as r:
                return r.status, await r.json(content_type=None)
        async with self.session.get(url) as r:
            return r.status, await r.json(content_type=None)

    @commands.hybrid_group(name="osint", invoke_without_command=True)
    async def osint(self, ctx: commands.Context):
        await ctx.send(
            "Usage: `,osint username <username>`, "
            "`,osint domain <domain>`, or `,osint ip <ip>`"
        )

    @osint.command(name="username", description="Look up a public Roblox username.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(username="Roblox username to look up")
    async def osint_username(self, ctx: commands.Context, *, username: str):
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
            return await ctx.send("Invalid Roblox username.")

        status, data = await self._json(
            "https://users.roblox.com/v1/usernames/users",
            method="POST",
            payload={"usernames": [username], "excludeBannedUsers": False},
        )

        if status != 200 or not data.get("data"):
            return await ctx.send("No Roblox user found.")

        user = data["data"][0]
        uid = user["id"]

        embed = discord.Embed(title="Roblox User", color=discord.Color.blurple())
        embed.add_field(name="Username", value=user.get("name", "Unknown"))
        embed.add_field(name="Display name", value=user.get("displayName", "Unknown"))
        embed.add_field(name="User ID", value=str(uid))
        embed.add_field(name="Profile", value=f"https://www.roblox.com/users/{uid}/profile", inline=False)
        await ctx.send(embed=embed)

    @osint.command(name="domain", description="Look up public DNS records for a domain.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(domain="Domain name to resolve")
    async def osint_domain(self, ctx: commands.Context, domain: str):
        domain = domain.strip().lower()
        if domain.startswith(("http://", "https://")):
            domain = urlparse(domain).hostname or ""

        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", domain):
            return await ctx.send("Invalid domain.")

        try:
            infos = await self.bot.loop.run_in_executor(None, lambda: socket.getaddrinfo(domain, None))
            ips = sorted({x[4][0] for x in infos})
        except socket.gaierror:
            return await ctx.send("Could not resolve that domain.")

        embed = discord.Embed(title="Domain Lookup", color=discord.Color.blurple())
        embed.add_field(name="Domain", value=domain, inline=False)
        embed.add_field(name="Resolved IPs", value="\n".join(ips[:10]) or "None")
        await ctx.send(embed=embed)

    @osint.command(name="ip", description="Look up public geolocation info for an IP address.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(ip="Public IP address to look up")
    async def osint_ip(self, ctx: commands.Context, ip: str):
        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError:
            return await ctx.send("Invalid IP address.")

        if addr.is_private or addr.is_loopback or addr.is_reserved:
            return await ctx.send("That is not a public IP address.")

        status, data = await self._json(f"https://ipwho.is/{addr}")
        if status != 200 or not data.get("success", True):
            return await ctx.send("Could not retrieve public IP metadata.")

        embed = discord.Embed(title="Public IP Lookup", color=discord.Color.blurple())
        embed.add_field(name="IP", value=str(addr))
        embed.add_field(name="Country", value=str(data.get("country", "Unknown")))
        embed.add_field(name="Region", value=str(data.get("region", "Unknown")))
        embed.add_field(name="City", value=str(data.get("city", "Unknown")))
        embed.add_field(name="ISP", value=str((data.get("connection") or {}).get("isp", "Unknown")), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roblox", description="Find the Roblox account linked to a Discord ID (requires a verification flow to populate).")
    @commands.cooldown(5, 10, commands.BucketType.user)
    @app_commands.describe(discord_id="Discord user ID to look up")
    async def roblox(self, ctx: commands.Context, discord_id: str):
        if not discord_id.isdigit():
            return await ctx.send("That doesn't look like a valid Discord ID.")
        user = self.roblox_links.get(int(discord_id))
        if not user:
            return await ctx.send(
                "No Roblox account is linked to that Discord ID. "
                "The user must link/verify their Roblox account first."
            )

        uid = user["id"]
        embed = discord.Embed(title="Discord → Roblox", color=discord.Color.blurple())
        embed.add_field(name="Discord ID", value=discord_id)
        embed.add_field(name="Roblox username", value=user.get("name", "Unknown"))
        embed.add_field(name="Display name", value=user.get("displayName", "Unknown"))
        embed.add_field(name="Roblox ID", value=str(uid))
        embed.add_field(name="Profile", value=f"https://www.roblox.com/users/{uid}/profile", inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OSINT(bot))
