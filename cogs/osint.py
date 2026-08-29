"""
Public-data lookup commands: Roblox profile lookup + multi-platform username
search, DNS resolution, public IP geolocation, and Instagram/TikTok public
stats. Everything here queries public data only (official public APIs where
they exist, and public profile pages otherwise) — no breach data,
credentials, or private personal information.

Commands:
    ,username <name>            -> rich Roblox profile lookup
    ,username hunter <name>     -> checks the name across several platforms
    ,domain lookup <domain>
    ,ip lookup <ip>
    ,instagram <username>
    ,tiktok <username>

Note on reliability: Instagram/TikTok/X/LinkedIn/Facebook don't offer free
public APIs, so those commands read data straight off the public profile
page. That's inherently fragile — those sites change their page structure
over time and sometimes block non-browser requests — so expect occasional
breakage there in a way the official-API-backed commands (Roblox, GitHub,
domain/IP) won't have.
"""

import asyncio
import ipaddress
import json
import html as html_lib
import re
import socket
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# platform name -> (profile URL template, whether a 2xx status reliably means "found")
HUNTER_PLATFORMS = {
    "YouTube": ("https://www.youtube.com/@{u}", True),
    "Instagram": ("https://www.instagram.com/{u}/", True),
    "X": ("https://x.com/{u}", False),  # X blocks non-browser requests inconsistently
    "GitHub": ("https://github.com/{u}", True),
    "BandLab": ("https://www.bandlab.com/{u}", True),
    "TikTok": ("https://www.tiktok.com/@{u}", True),
    "LinkedIn": ("https://www.linkedin.com/in/{u}", False),  # usually login-walled regardless
    "Facebook": ("https://www.facebook.com/{u}", False),  # usually login-walled regardless
}


class OSINT(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), headers=BROWSER_HEADERS)

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def _json(self, url, *, method="GET", payload=None):
        if method == "POST":
            async with self.session.post(url, json=payload) as r:
                return r.status, await r.json(content_type=None)
        async with self.session.get(url) as r:
            return r.status, await r.json(content_type=None)

    # ==================== username / roblox ====================

    @commands.hybrid_group(name="username", aliases=["roblox"], invoke_without_command=True, description="Look up a Roblox profile by username.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(name="Roblox username to look up")
    async def username(self, ctx: commands.Context, *, name: str):
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
            return await ctx.send("Invalid Roblox username.")

        status, data = await self._json(
            "https://users.roblox.com/v1/usernames/users",
            method="POST",
            payload={"usernames": [name], "excludeBannedUsers": False},
        )
        if status != 200 or not data.get("data"):
            return await ctx.send("No Roblox user found.")

        user = data["data"][0]
        uid = user["id"]

        profile_url = f"https://www.roblox.com/users/{uid}/profile"
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{user.get('displayName', name)} (@{user.get('name', name)})", url=profile_url)

        try:
            status2, details = await self._json(f"https://users.roblox.com/v1/users/{uid}")
            if status2 == 200:
                created = details.get("created", "")[:10]
                embed.add_field(name="Created", value=created or "Unknown")
        except Exception:
            pass

        try:
            status3, badges = await self._json(f"https://accountinformation.roblox.com/v1/users/{uid}/roblox-badges")
            if status3 == 200 and isinstance(badges, list):
                embed.add_field(name=f"Badges ({len(badges)})", value=", ".join(b.get("name", "?") for b in badges[:5]) or "None")
        except Exception:
            pass

        embed.add_field(name="ID", value=str(uid))

        try:
            status4, followers = await self._json(f"https://friends.roblox.com/v1/users/{uid}/followers/count")
            status5, following = await self._json(f"https://friends.roblox.com/v1/users/{uid}/followings/count")
            if status4 == 200:
                embed.add_field(name="Followers", value=str(followers.get("count", "Unknown")))
            if status5 == 200:
                embed.add_field(name="Following", value=str(following.get("count", "Unknown")))
        except Exception:
            pass

        try:
            status6, thumb = await self._json(
                f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=png"
            )
            if status6 == 200 and thumb.get("data"):
                embed.set_thumbnail(url=thumb["data"][0]["imageUrl"])
        except Exception:
            pass

        embed.add_field(name="Profile", value=f"[View Roblox profile]({profile_url})", inline=False)
        await ctx.send(embed=embed)

    @username.command(name="hunter", description="Check a username across several platforms at once.")
    @commands.cooldown(1, 20, commands.BucketType.user)
    @app_commands.describe(name="Username to search for")
    async def username_hunter(self, ctx: commands.Context, *, name: str):
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,30}", name):
            return await ctx.send("Invalid username — letters, numbers, `.`, `_`, and `-` only.")

        if ctx.interaction:
            await ctx.interaction.response.defer()

        results = []

        async def check(platform, template, reliable):
            url = template.format(u=name)
            try:
                async with self.session.get(url, allow_redirects=True) as resp:
                    if resp.status < 400:
                        results.append((platform, "✅ Found" if reliable else "❔ Possibly found (unreliable check)", url))
                    elif resp.status == 404:
                        results.append((platform, "❌ Not found", None))
                    else:
                        results.append((platform, f"❔ Unknown (status {resp.status})", None))
            except Exception:
                results.append((platform, "❔ Couldn't check (blocked/timed out)", None))

        await asyncio.gather(*(check(p, t, r) for p, (t, r) in HUNTER_PLATFORMS.items()))

        # Roblox via the real API — reliable, so handled separately.
        try:
            status, data = await self._json(
                "https://users.roblox.com/v1/usernames/users",
                method="POST",
                payload={"usernames": [name], "excludeBannedUsers": False},
            )
            if status == 200 and data.get("data"):
                uid = data["data"][0]["id"]
                results.append(("Roblox", "✅ Found", f"https://www.roblox.com/users/{uid}/profile"))
            else:
                results.append(("Roblox", "❌ Not found", None))
        except Exception:
            results.append(("Roblox", "❔ Couldn't check", None))

        results.append(("Discord", "⚠️ No public lookup available", None))

        embed = discord.Embed(title=f"Username Hunter: {name}", color=discord.Color.blurple())
        embed.description = (
            "\n".join(f"**{p}** — {status}" + (f"\n{url}" if url else "") for p, status, url in results)
        )
        embed.set_footer(text="Results for X, LinkedIn, and Facebook are unreliable — those sites often return the same status whether or not the account exists.")
        await ctx.send(embed=embed)

    # ==================== domain / ip ====================

    @commands.hybrid_group(name="domain", invoke_without_command=True)
    async def domain(self, ctx: commands.Context):
        await ctx.send("Usage: `,domain lookup <domain>`")

    @domain.command(name="lookup", description="Look up public DNS records for a domain.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(domain="Domain name to resolve")
    async def domain_lookup(self, ctx: commands.Context, domain: str):
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

    @commands.hybrid_group(name="ip", invoke_without_command=True)
    async def ip(self, ctx: commands.Context):
        await ctx.send("Usage: `,ip lookup <ip address>`")

    @ip.command(name="lookup", description="Look up public geolocation info for an IP address.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(address="Public IP address to look up")
    async def ip_lookup(self, ctx: commands.Context, address: str):
        try:
            addr = ipaddress.ip_address(address.strip())
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

    # ==================== instagram ====================

    @commands.hybrid_command(name="instagram", description="Look up public Instagram profile stats.")
    @commands.cooldown(3, 15, commands.BucketType.user)
    @app_commands.describe(username="Instagram username")
    async def instagram(self, ctx: commands.Context, username: str):
        username = username.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
            return await ctx.send("Invalid Instagram username.")

        # Instagram's undocumented public profile endpoint is fragile and can
        # rate-limit datacenter IPs. Try both current hostnames and a mobile-ish
        # browser UA before falling back to the normal profile HTML.
        api_headers = dict(BROWSER_HEADERS)
        api_headers.update({
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.instagram.com/{username}/",
        })

        for endpoint in (
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}",
        ):
            try:
                async with self.session.get(endpoint, headers=api_headers) as resp:
                    if resp.status == 404:
                        return await ctx.send("No Instagram account found with that username.")
                    if resp.status != 200:
                        continue
                    data = await resp.json(content_type=None)
                    user = (data.get("data") or {}).get("user")
                    if not user:
                        continue

                    followers = (user.get("edge_followed_by") or {}).get("count")
                    following = (user.get("edge_follow") or {}).get("count")
                    posts = (user.get("edge_owner_to_timeline_media") or {}).get("count")
                    private = bool(user.get("is_private"))
                    display_name = user.get("full_name") or username

                    profile_url = f"https://www.instagram.com/{username}/"
                    title = f"{display_name} (@{username})"
                    if private:
                        title = f"🔒 {title}"
                    embed = discord.Embed(
                        color=discord.Color.from_rgb(225, 48, 108),
                    )
                    embed.set_author(name=title, url=profile_url)
                    # Match the compact profile-card layout from the reference:
                    # Posts → Following → Followers.
                    embed.add_field(name="Posts", value=str(posts if posts is not None else 0), inline=False)
                    embed.add_field(name="Following", value=str(following if following is not None else "Unknown"), inline=False)
                    embed.add_field(name="Followers", value=str(followers if followers is not None else "Unknown"), inline=False)
                    pic = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
                    if pic:
                        embed.set_thumbnail(url=pic)
                    if private:
                        embed.set_footer(text="Private account")
                    embed.add_field(name="Instagram", value=f"https://www.instagram.com/{username}/", inline=False)
                    return await ctx.send(embed=embed)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, json.JSONDecodeError):
                continue

        # Fallback: parse the public profile page's OpenGraph metadata. This
        # still works when the JSON endpoint is temporarily rate-limited.
        try:
            async with self.session.get(
                f"https://www.instagram.com/{username}/",
                headers=BROWSER_HEADERS,
                allow_redirects=True,
            ) as resp:
                if resp.status == 404:
                    return await ctx.send("No Instagram account found with that username.")
                if resp.status != 200:
                    return await ctx.send(
                        "Instagram is currently blocking profile lookups from the bot's network. Try again later."
                    )
                html = await resp.text(errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return await ctx.send("Couldn't reach Instagram right now.")

        def meta(property_name: str):
            patterns = [
                rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']*)["\']',
                rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    return html_lib.unescape(match.group(1))
            return None

        description = meta("og:description") or ""
        image_url = meta("og:image")
        title_text = meta("og:title") or f"@{username}"

        # Instagram may return a valid profile HTML page without OpenGraph
        # description data (common when the request comes from a datacenter).
        # Do not incorrectly call it private or a layout failure. Try JSON-LD
        # for the display name/avatar, then return a usable profile card with
        # Unknown stats if Instagram has hidden the counters.
        try:
            for match in re.finditer(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html,
                re.IGNORECASE | re.DOTALL,
            ):
                obj = json.loads(match.group(1))
                objects = obj if isinstance(obj, list) else [obj]
                for item in objects:
                    if not isinstance(item, dict):
                        continue
                    if item.get("name") and title_text == f"@{username}":
                        title_text = str(item["name"])
                    image = item.get("image")
                    if isinstance(image, str) and not image_url:
                        image_url = image
                    elif isinstance(image, dict) and not image_url:
                        image_url = image.get("url")
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        # Instagram has used both "Followers, Following, Posts" and other
        # orderings in its metadata, so parse each stat independently.
        def stat(label: str):
            match = re.search(rf"([\d,.]+(?:[KMB])?)\s+{label}", description, re.IGNORECASE)
            return match.group(1) if match else "Unknown"

        private = bool(re.search(r"private", description, re.IGNORECASE))
        profile_url = f"https://www.instagram.com/{username}/"
        embed = discord.Embed(
            color=discord.Color.from_rgb(225, 48, 108),
        )
        embed.set_author(name=("🔒 " if private else "") + title_text, url=profile_url)
        embed.add_field(name="Posts", value=stat("Posts"), inline=False)
        embed.add_field(name="Following", value=stat("Following"), inline=False)
        embed.add_field(name="Followers", value=stat("Followers"), inline=False)
        if image_url:
            embed.set_thumbnail(url=image_url)
        if private:
            embed.set_footer(text="Private account")
        embed.add_field(name="Instagram", value=f"https://www.instagram.com/{username}/", inline=False)
        await ctx.send(embed=embed)

    # ==================== tiktok ====================

    @commands.hybrid_command(name="tiktok", description="Look up public TikTok profile stats.")
    @commands.cooldown(3, 15, commands.BucketType.user)
    @app_commands.describe(username="TikTok username")
    async def tiktok(self, ctx: commands.Context, username: str):
        username = username.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
            return await ctx.send("Invalid TikTok username.")

        try:
            async with self.session.get(f"https://www.tiktok.com/@{username}") as resp:
                if resp.status == 404:
                    return await ctx.send("No TikTok account found with that username.")
                if resp.status != 200:
                    return await ctx.send(f"TikTok returned an unexpected response (status {resp.status}) — it may be blocking the request.")
                html = await resp.text()
        except Exception:
            return await ctx.send("Couldn't reach TikTok right now.")

        match = re.search(
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not match:
            return await ctx.send("Couldn't read that profile — it may be private, or TikTok changed its page layout.")

        try:
            data = json.loads(match.group(1))
            user_detail = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]
            user_info = user_detail["userInfo"]
            stats = user_info["stats"]
            user = user_info["user"]
        except (KeyError, json.JSONDecodeError):
            return await ctx.send("Couldn't parse that profile's data — TikTok may have changed its page layout.")

        profile_url = f"https://www.tiktok.com/@{username}"
        embed = discord.Embed(color=discord.Color.dark_teal())
        embed.set_author(name=f"{user.get('nickname', username)} (@{username})", url=profile_url)
        embed.add_field(name="Likes", value=str(stats.get("heartCount", "Unknown")))
        embed.add_field(name="Followers", value=str(stats.get("followerCount", "Unknown")))
        embed.add_field(name="Following", value=str(stats.get("followingCount", "Unknown")))
        if user.get("avatarLarger"):
            embed.set_thumbnail(url=user["avatarLarger"])
        embed.add_field(name="Profile", value=f"[View TikTok profile]({profile_url})", inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OSINT(bot))
